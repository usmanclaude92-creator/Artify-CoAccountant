//! The single gateway to the Artify cloud API. Tokens never leave this module: the access token is held in memory only,
//! the rotating refresh token lives in the OS vault, and the webview receives data, never credentials. The backend stays
//! the authority for authentication, permissions and tenant isolation; this client only transports and classifies.

use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::StreamExt;
use reqwest::{header, Method, StatusCode};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use tokio::sync::Mutex as AsyncMutex;
use tokio_util::io::ReaderStream;

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

struct Tokens {
    access: Option<String>,
    generation: u64,
}

/// What a finished download proved about the bytes that arrived.
#[derive(Debug, Clone)]
pub struct Downloaded {
    pub size: u64,
    pub sha256: String,
    /// The checksum the server announced (`X-Document-Sha256`), when present.
    pub announced_sha256: Option<String>,
}

pub struct ApiClient {
    http: reqwest::Client,
    /// Same identity, but no overall timeout: documents can take minutes. A stalled connection still fails (read timeout).
    transfer_http: reqwest::Client,
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
        let transfer_http = reqwest::Client::builder()
            .connect_timeout(cfg.connect_timeout)
            .read_timeout(Duration::from_secs(60))
            .user_agent(format!("ArtifyCoAccountantDesktop/{}", cfg.app_version))
            .build()
            .map_err(|_| AppError::new("config_error", "The network client could not be created."))?;
        Ok(Self {
            http,
            transfer_http,
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
        match self.once(&Method::POST, "/auth/refresh", None, Some(&body), None, None).await {
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
    pub async fn put(&self, path: &str, org: Option<&str>, body: Value) -> Result<Response> {
        self.send(Method::PUT, path, org, Some(body), None, true).await
    }
    pub async fn delete(&self, path: &str, org: Option<&str>) -> Result<Response> {
        self.send(Method::DELETE, path, org, None, None, true).await
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
        let idempotent = method == Method::GET;
        let mut refreshed = false;
        let mut attempt = 0u32;
        loop {
            let (token, generation) = self.access();
            if auth && token.is_none() {
                return Err(AppError::session_expired());
            }
            let outcome = self.once(&method, path, org, body.as_ref(), etag, if auth { token.as_deref() } else { None }).await;
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

    async fn once(
        &self,
        method: &Method,
        path: &str,
        org: Option<&str>,
        body: Option<&Value>,
        etag: Option<&str>,
        token: Option<&str>,
    ) -> Result<Response> {
        let url = format!("{}{}", self.cfg.base_url, path);
        let mut req = self.http.request(method.clone(), &url).header(header::ACCEPT, "application/json, application/problem+json");
        if let Some(t) = token {
            req = req.bearer_auth(t);
        }
        if let Some(o) = org {
            req = req.header("X-Organization-Id", o);
        }
        if let Some(e) = etag {
            req = req.header(header::IF_NONE_MATCH, e);
        }
        if let Some(b) = body {
            req = req.json(b);
        }
        let res = req.send().await.map_err(net_error)?;
        read_response(res).await
    }

    // ------------------------------------------------------------------ streamed transfers (documents)
    /// Stream a local file as the body of a PUT. Progress is reported in bytes sent; setting `cancel` aborts the transfer.
    /// The file is opened per attempt, so a retry after a token refresh starts from the beginning (no partial state).
    pub async fn put_file(
        &self,
        path: &str,
        org: Option<&str>,
        file: &Path,
        size: u64,
        cancel: Arc<AtomicBool>,
        progress: Arc<dyn Fn(u64) + Send + Sync>,
    ) -> Result<Response> {
        self.send_file(Method::PUT, path, org, file, 0, size, cancel, progress).await
    }

    /// Resumable upload: stream `len` bytes of `file` starting at `start` as the body of a PATCH (one chunk). Progress counts
    /// bytes of this chunk only. The server decides where the chunk belongs (`?offset=` in `path`) and answers 409
    /// `offset_mismatch` with the offset it holds when the two disagree.
    #[allow(clippy::too_many_arguments)]
    pub async fn patch_file_range(
        &self,
        path: &str,
        org: Option<&str>,
        file: &Path,
        start: u64,
        len: u64,
        cancel: Arc<AtomicBool>,
        progress: Arc<dyn Fn(u64) + Send + Sync>,
    ) -> Result<Response> {
        self.send_file(Method::PATCH, path, org, file, start, len, cancel, progress).await
    }

    #[allow(clippy::too_many_arguments)]
    async fn send_file(
        &self,
        method: Method,
        path: &str,
        org: Option<&str>,
        file: &Path,
        start: u64,
        len: u64,
        cancel: Arc<AtomicBool>,
        progress: Arc<dyn Fn(u64) + Send + Sync>,
    ) -> Result<Response> {
        use tokio::io::{AsyncReadExt, AsyncSeekExt};
        let mut refreshed = false;
        loop {
            let (token, generation) = self.access();
            let token = token.ok_or_else(AppError::session_expired)?;
            let mut f = tokio::fs::File::open(file).await.map_err(AppError::from)?;
            if start > 0 {
                f.seek(std::io::SeekFrom::Start(start)).await.map_err(AppError::from)?;
            }
            let sent = Arc::new(AtomicU64::new(0));
            let (c2, p2, s2) = (cancel.clone(), progress.clone(), sent.clone());
            let stream = ReaderStream::with_capacity(f.take(len), 256 * 1024).map(move |chunk| {
                if c2.load(Ordering::Relaxed) {
                    return Err(std::io::Error::new(std::io::ErrorKind::Interrupted, "cancelled"));
                }
                let chunk = chunk?;
                p2(s2.fetch_add(chunk.len() as u64, Ordering::Relaxed) + chunk.len() as u64);
                Ok(chunk)
            });
            let mut req = self
                .transfer_http
                .request(method.clone(), format!("{}{}", self.cfg.base_url, path))
                .bearer_auth(&token)
                .header(header::ACCEPT, "application/json, application/problem+json")
                .header(header::CONTENT_TYPE, "application/octet-stream")
                .header(header::CONTENT_LENGTH, len)
                .body(reqwest::Body::wrap_stream(stream));
            if let Some(o) = org {
                req = req.header("X-Organization-Id", o);
            }
            let outcome = match req.send().await {
                Ok(res) => read_response(res).await,
                Err(_) if cancel.load(Ordering::Relaxed) => Err(AppError::new("cancelled", "The upload was cancelled.")),
                Err(e) => Err(net_error(e)),
            };
            match outcome {
                Err(e) if e.status == Some(401) && !refreshed => {
                    refreshed = true;
                    self.refresh_now(generation).await?;
                }
                other => return other,
            }
        }
    }

    /// Stream a GET response into `dest` (the caller supplies a temporary name and renames after verifying). Returns the
    /// SHA-256 of what arrived and the checksum the server announced, so the caller can refuse a damaged download.
    pub async fn download_to(
        &self,
        path: &str,
        org: Option<&str>,
        dest: &Path,
        cancel: Arc<AtomicBool>,
        progress: Arc<dyn Fn(u64) + Send + Sync>,
    ) -> Result<Downloaded> {
        let mut refreshed = false;
        loop {
            let (token, generation) = self.access();
            let token = token.ok_or_else(AppError::session_expired)?;
            let mut req = self
                .transfer_http
                .get(format!("{}{}", self.cfg.base_url, path))
                .bearer_auth(&token)
                .header(header::ACCEPT, "application/octet-stream, application/problem+json");
            if let Some(o) = org {
                req = req.header("X-Organization-Id", o);
            }
            let res = req.send().await.map_err(net_error)?;
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
            let announced = res
                .headers()
                .get("x-document-sha256")
                .or_else(|| res.headers().get("x-export-sha256"))
                .and_then(|v| v.to_str().ok())
                .map(str::to_string);
            let mut file = tokio::fs::File::create(dest).await.map_err(AppError::from)?;
            let mut hasher = Sha256::new();
            let mut total = 0u64;
            let mut stream = res.bytes_stream();
            while let Some(chunk) = stream.next().await {
                if cancel.load(Ordering::Relaxed) {
                    drop(file);
                    let _ = tokio::fs::remove_file(dest).await;
                    return Err(AppError::new("cancelled", "The download was cancelled."));
                }
                let chunk = match chunk {
                    Ok(c) => c,
                    Err(_) => {
                        drop(file);
                        let _ = tokio::fs::remove_file(dest).await;
                        return Err(AppError::network("The connection was interrupted.").retryable());
                    }
                };
                hasher.update(&chunk);
                total += chunk.len() as u64;
                file.write_all(&chunk).await.map_err(AppError::from)?;
                progress(total);
            }
            file.flush().await.map_err(AppError::from)?;
            return Ok(Downloaded { size: total, sha256: hex(&hasher.finalize()), announced_sha256: announced });
        }
    }
}

fn net_error(e: reqwest::Error) -> AppError {
    if e.is_timeout() {
        AppError::timeout()
    } else {
        AppError::network("The server cannot be reached. Check your internet connection.")
    }
}

async fn read_response(res: reqwest::Response) -> Result<Response> {
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

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
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
