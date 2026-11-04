//! The single gateway to the Artify cloud API. Tokens never leave this module: the access token is held in memory only,
//! the rotating refresh token lives in the OS vault, and the webview receives data, never credentials. The backend stays
//! the authority for authentication, permissions and tenant isolation; this client only transports and classifies.

use std::io::Write;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use reqwest::{header, Method, StatusCode};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::sync::Mutex as AsyncMutex;

use crate::error::{AppError, Result};
use crate::vault::SecretStore;

#[derive(Debug, Clone)]
pub struct ApiConfig {
    /// e.g. `https://api.example.com/api/v1` (no trailing slash needed).
    pub base_url: String,
    pub connect_timeout: Duration,
    pub request_timeout: Duration,
    /// Extra attempts for transient failures of idempotent (GET) requests.
    pub max_retries: u32,
    pub retry_base: Duration,
    pub app_version: String,
}

impl ApiConfig {
    pub fn new(base_url: &str, app_version: &str) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            connect_timeout: Duration::from_secs(5),
            request_timeout: Duration::from_secs(20),
            max_retries: 2,
            retry_base: Duration::from_millis(250),
            app_version: app_version.into(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Response {
    pub status: u16,
    pub etag: Option<String>,
    pub not_modified: bool,
    pub body: Value,
}

#[derive(Debug, Clone)]
pub struct Downloaded {
    pub size: u64,
    pub sha256: String,
    pub content_type: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LoginOutcome {
    Authenticated,
    MfaRequired { mfa_token: String },
    MfaEnrollmentRequired,
}

#[derive(Deserialize)]
struct TokenPair {
    access_token: String,
    refresh_token: String,
}

#[derive(Deserialize)]
struct LoginBody {
    status: String,
    tokens: Option<TokenPair>,
    mfa_token: Option<String>,
    #[serde(default)]
    mfa_enrollment_required: bool,
}

/// Extras for the few requests that are not plain JSON: an idempotency key, or a raw binary body (an upload chunk).
#[derive(Default, Clone)]
struct Opts {
    idem_key: Option<String>,
    raw: Option<Vec<u8>>,
    transfer: bool,
}

struct Tokens {
    access: Option<String>,
    generation: u64,
}

pub struct ApiClient {
    http: reqwest::Client,
    /// For file transfers: no total timeout (a large file legitimately takes minutes), only a stall (read) timeout.
    transfer: reqwest::Client,
    cfg: ApiConfig,
    store: Arc<dyn SecretStore>,
    tokens: Mutex<Tokens>,
    refresh_gate: AsyncMutex<()>,
}

impl ApiClient {
    pub fn new(cfg: ApiConfig, store: Arc<dyn SecretStore>) -> Result<Self> {
        let http = reqwest::Client::builder()
            .connect_timeout(cfg.connect_timeout)
            .timeout(cfg.request_timeout)
            .user_agent(format!("ArtifyCoAccountantDesktop/{}", cfg.app_version))
            .build()
            .map_err(|_| AppError::new("config_error", "The network client could not be created."))?;
        let transfer = reqwest::Client::builder()
            .connect_timeout(cfg.connect_timeout)
            .read_timeout(Duration::from_secs(60))
            .user_agent(format!("ArtifyCoAccountantDesktop/{}", cfg.app_version))
            .build()
            .map_err(|_| AppError::new("config_error", "The network client could not be created."))?;
        Ok(Self {
            http,
            transfer,
            cfg,
            store,
            tokens: Mutex::new(Tokens { access: None, generation: 0 }),
            refresh_gate: AsyncMutex::new(()),
        })
    }

    pub fn base_url(&self) -> &str {
        &self.cfg.base_url
    }
    fn vault_key(&self) -> String {
        format!("refresh-token:{}", self.cfg.base_url)
    }
    pub fn is_authenticated(&self) -> bool {
        self.tokens.lock().map(|t| t.access.is_some()).unwrap_or(false)
    }
    fn access(&self) -> (Option<String>, u64) {
        self.tokens.lock().map(|t| (t.access.clone(), t.generation)).unwrap_or((None, 0))
    }
    fn set_tokens(&self, pair: TokenPair) -> Result<()> {
        // store the rotated refresh token BEFORE using the new access token: a crash can then never lose the session
        self.store.set(&self.vault_key(), &pair.refresh_token)?;
        if let Ok(mut t) = self.tokens.lock() {
            t.access = Some(pair.access_token);
            t.generation += 1;
        }
        Ok(())
    }
    fn clear_tokens(&self) {
        if let Ok(mut t) = self.tokens.lock() {
            t.access = None;
            t.generation += 1;
        }
        let _ = self.store.delete(&self.vault_key());
    }

    // ------------------------------------------------------------------ authentication
    pub async fn login(&self, email: &str, password: &str) -> Result<LoginOutcome> {
        let res = self.send(Method::POST, "/auth/login", None, Some(json!({ "email": email, "password": password })), None, false).await?;
        let body: LoginBody = serde_json::from_value(res.body).map_err(|_| AppError::malformed("login"))?;
        match (body.status.as_str(), body.tokens, body.mfa_token) {
            ("ok", Some(t), _) => {
                self.set_tokens(t)?;
                Ok(LoginOutcome::Authenticated)
            }
            ("mfa_required", _, Some(mfa_token)) => Ok(LoginOutcome::MfaRequired { mfa_token }),
            ("mfa_required", _, None) if body.mfa_enrollment_required => Ok(LoginOutcome::MfaEnrollmentRequired),
            _ => Err(AppError::malformed("login")),
        }
    }

    pub async fn verify_mfa(&self, mfa_token: &str, code: &str) -> Result<()> {
        let res =
            self.send(Method::POST, "/auth/mfa/verify", None, Some(json!({ "mfa_token": mfa_token, "code": code })), None, false).await?;
        let pair: TokenPair = serde_json::from_value(res.body).map_err(|_| AppError::malformed("mfa"))?;
        self.set_tokens(pair)
    }

    /// Resume a previous session from the vault. `Ok(false)`: nothing stored or the session is over (stored token removed).
    /// `Err`: could not decide (offline, server down) — the stored token is kept so a later retry can still succeed.
    pub async fn restore(&self) -> Result<bool> {
        if self.store.get(&self.vault_key())?.is_none() {
            return Ok(false);
        }
        match self.refresh_now(self.access().1).await {
            Ok(()) => Ok(true),
            Err(e) if e.is_session_expired() => Ok(false),
            Err(e) => Err(e),
        }
    }

    /// Single-flight refresh: concurrent 401s trigger one rotation. `seen_generation` is the token generation the caller
    /// used; if it changed while waiting, someone else already refreshed.
    async fn refresh_now(&self, seen_generation: u64) -> Result<()> {
        let _gate = self.refresh_gate.lock().await;
        if self.access().1 != seen_generation && self.is_authenticated() {
            return Ok(());
        }
        let Some(refresh) = self.store.get(&self.vault_key())? else {
            self.clear_tokens();
            return Err(AppError::session_expired());
        };
        let body = json!({ "refresh_token": refresh });
        match self.once(&Method::POST, "/auth/refresh", None, Some(&body), None, None, &Opts::default()).await {
            Ok(res) => {
                let pair: TokenPair = serde_json::from_value(res.body).map_err(|_| AppError::malformed("refresh"))?;
                self.set_tokens(pair)
            }
            Err(e) if e.status == Some(401) || e.status == Some(403) => {
                // ended, expired, reused, or the device was revoked: the session cannot continue
                self.clear_tokens();
                Err(AppError::session_expired())
            }
            Err(e) => Err(e),
        }
    }

    pub async fn logout(&self) {
        if self.is_authenticated() {
            let _ = self.send(Method::POST, "/auth/logout", None, None, None, true).await;
        }
        self.clear_tokens();
    }

    // ------------------------------------------------------------------ requests
    pub async fn get(&self, path: &str, org: Option<&str>) -> Result<Response> {
        self.send(Method::GET, path, org, None, None, true).await
    }
    pub async fn get_if_changed(&self, path: &str, org: Option<&str>, etag: Option<&str>) -> Result<Response> {
        self.send(Method::GET, path, org, None, etag, true).await
    }
    pub async fn post(&self, path: &str, org: Option<&str>, body: Option<Value>) -> Result<Response> {
        self.send(Method::POST, path, org, body, None, true).await
    }
    pub async fn patch(&self, path: &str, org: Option<&str>, body: Value) -> Result<Response> {
        self.send(Method::PATCH, path, org, Some(body), None, true).await
    }
    pub async fn delete(&self, path: &str, org: Option<&str>) -> Result<Response> {
        self.send(Method::DELETE, path, org, None, None, true).await
    }

    /// POST that is safe to repeat: the server returns the original result for the same key.
    pub async fn post_keyed(&self, path: &str, org: Option<&str>, body: Value, idem_key: &str) -> Result<Response> {
        let opts = Opts { idem_key: Some(idem_key.to_string()), ..Opts::default() };
        self.send_opts(Method::POST, path, org, Some(body), None, true, &opts).await
    }
    /// Send one chunk of a resumable upload as raw bytes.
    pub async fn put_chunk(&self, path: &str, org: Option<&str>, bytes: Vec<u8>) -> Result<Response> {
        let opts = Opts { raw: Some(bytes), transfer: true, ..Opts::default() };
        self.send_opts(Method::PUT, path, org, None, None, true, &opts).await
    }

    /// Stream a file to `dest` (never into memory), computing its SHA-256 on the way. The caller verifies the digest
    /// against the server's `X-Content-SHA256`; this function reports it and fails if the server's header disagrees.
    pub async fn download_to(&self, path: &str, org: Option<&str>, dest: &Path) -> Result<Downloaded> {
        let mut refreshed = false;
        loop {
            let (token, generation) = self.access();
            let Some(token) = token else { return Err(AppError::session_expired()) };
            let url = format!("{}{}", self.cfg.base_url, path);
            let mut req = self.transfer.get(&url).bearer_auth(&token);
            if let Some(o) = org {
                req = req.header("X-Organization-Id", o);
            }
            let mut res = req.send().await.map_err(|e| {
                if e.is_timeout() {
                    AppError::timeout()
                } else {
                    AppError::network("The server cannot be reached. Check your internet connection.")
                }
            })?;
            let status = res.status();
            if status == StatusCode::UNAUTHORIZED && !refreshed {
                refreshed = true;
                self.refresh_now(generation).await?;
                continue;
            }
            if !status.is_success() {
                let bytes = res.bytes().await.unwrap_or_default();
                return Err(problem(status, &bytes));
            }
            let header_str = |n: &str| res.headers().get(n).and_then(|v| v.to_str().ok()).map(str::to_string);
            let expected = header_str("x-content-sha256");
            let content_type = header_str("content-type").unwrap_or_default();
            let mut file =
                std::fs::File::create(dest).map_err(|_| AppError::new("local_io", "The file could not be created on this computer."))?;
            let mut hasher = Sha256::new();
            let mut size = 0u64;
            let write_failed = || AppError::new("local_io", "The file could not be written to this computer (is the disk full?).");
            while let Some(chunk) = res.chunk().await.map_err(|_| AppError::network("The connection was interrupted.").retryable())? {
                file.write_all(&chunk).map_err(|_| write_failed())?;
                hasher.update(&chunk);
                size += chunk.len() as u64;
            }
            file.sync_all().map_err(|_| write_failed())?;
            let sha256: String = hasher.finalize().iter().map(|b| format!("{b:02x}")).collect();
            if expected.as_deref().is_some_and(|e| e != sha256) {
                let _ = std::fs::remove_file(dest);
                return Err(
                    AppError::new("integrity_error", "The downloaded file does not match its checksum and was discarded.").retryable()
                );
            }
            return Ok(Downloaded { size, sha256, content_type });
        }
    }

    async fn send(
        &self,
        method: Method,
        path: &str,
        org: Option<&str>,
        body: Option<Value>,
        etag: Option<&str>,
        auth: bool,
    ) -> Result<Response> {
        self.send_opts(method, path, org, body, etag, auth, &Opts::default()).await
    }

    #[allow(clippy::too_many_arguments)]
    async fn send_opts(
        &self,
        method: Method,
        path: &str,
        org: Option<&str>,
        body: Option<Value>,
        etag: Option<&str>,
        auth: bool,
        opts: &Opts,
    ) -> Result<Response> {
        // GET is safe to repeat; a chunk PUT is too (the server refuses a wrong offset); a POST carrying an idempotency key is too
        let idempotent = method == Method::GET || method == Method::PUT || opts.idem_key.is_some();
        let mut refreshed = false;
        let mut attempt = 0u32;
        loop {
            let (token, generation) = self.access();
            if auth && token.is_none() {
                return Err(AppError::session_expired());
            }
            let outcome = self.once(&method, path, org, body.as_ref(), etag, if auth { token.as_deref() } else { None }, opts).await;
            match outcome {
                Err(e) if auth && e.status == Some(401) && !refreshed => {
                    refreshed = true;
                    self.refresh_now(generation).await?;
                    continue;
                }
                Err(e) if e.retryable && idempotent && attempt < self.cfg.max_retries => {
                    attempt += 1;
                    let backoff = self.cfg.retry_base * 2u32.pow(attempt - 1);
                    let jitter = Duration::from_millis(rand_jitter(backoff.as_millis() as u64 / 4 + 1));
                    tokio::time::sleep(backoff + jitter).await;
                    continue;
                }
                other => return other,
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    async fn once(
        &self,
        method: &Method,
        path: &str,
        org: Option<&str>,
        body: Option<&Value>,
        etag: Option<&str>,
        token: Option<&str>,
        opts: &Opts,
    ) -> Result<Response> {
        let url = format!("{}{}", self.cfg.base_url, path);
        let client = if opts.transfer { &self.transfer } else { &self.http };
        let mut req = client.request(method.clone(), &url).header(header::ACCEPT, "application/json, application/problem+json");
        if let Some(t) = token {
            req = req.bearer_auth(t);
        }
        if let Some(o) = org {
            req = req.header("X-Organization-Id", o);
        }
        if let Some(e) = etag {
            req = req.header(header::IF_NONE_MATCH, e);
        }
        if let Some(k) = &opts.idem_key {
            req = req.header("Idempotency-Key", k);
        }
        if let Some(b) = body {
            req = req.json(b);
        }
        if let Some(raw) = &opts.raw {
            req = req.header(header::CONTENT_TYPE, "application/octet-stream").body(raw.clone());
        }
        let res = req.send().await.map_err(|e| {
            if e.is_timeout() {
                AppError::timeout()
            } else {
                AppError::network("The server cannot be reached. Check your internet connection.")
            }
        })?;
        let status = res.status();
        let etag_out = res.headers().get(header::ETAG).and_then(|v| v.to_str().ok()).map(str::to_string);
        if status == StatusCode::NOT_MODIFIED {
            return Ok(Response { status: 304, etag: etag_out, not_modified: true, body: Value::Null });
        }
        let bytes = res.bytes().await.map_err(|_| AppError::network("The connection was interrupted.").retryable())?;
        if status.is_success() {
            let body =
                if bytes.is_empty() { Value::Null } else { serde_json::from_slice(&bytes).map_err(|_| AppError::malformed("not JSON"))? };
            return Ok(Response { status: status.as_u16(), etag: etag_out, not_modified: false, body });
        }
        Err(problem(status, &bytes))
    }
}

fn rand_jitter(max: u64) -> u64 {
    use rand::Rng;
    rand::thread_rng().gen_range(0..max.max(1))
}

/// Translate an error response (RFC 9457 problem+json from the backend) without keeping the raw body.
fn problem(status: StatusCode, bytes: &[u8]) -> AppError {
    let v: Value = serde_json::from_slice(bytes).unwrap_or(Value::Null);
    let code = v.get("code").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| default_code(status).to_string());
    let detail = v.get("detail").and_then(Value::as_str).unwrap_or("The request could not be completed.");
    let mut e = AppError::new(&code, detail).with_status(status.as_u16());
    e.retryable = matches!(status.as_u16(), 429 | 502 | 503 | 504);
    // keep only small structured extras (limit info, current_version, validation errors), never headers or bodies
    if let Some(obj) = v.as_object() {
        let extras: serde_json::Map<String, Value> = obj
            .iter()
            .filter(|(k, _)| !matches!(k.as_str(), "type" | "title" | "status" | "code" | "detail" | "instance" | "correlation_id"))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        if !extras.is_empty() {
            e.detail = Some(Value::Object(extras));
        }
    }
    e
}

fn default_code(s: StatusCode) -> &'static str {
    match s.as_u16() {
        401 => "unauthenticated",
        403 => "forbidden",
        404 => "not_found",
        409 => "conflict",
        402 => "entitlement_exceeded",
        422 => "validation_error",
        429 => "rate_limited",
        500..=599 => "server_error",
        _ => "http_error",
    }
}
