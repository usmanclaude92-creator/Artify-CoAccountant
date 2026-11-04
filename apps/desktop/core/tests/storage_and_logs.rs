use std::fs;
use std::path::Path;

use artify_core::logs::{redact, Logger};
use artify_core::storage::*;

#[test]
fn sanitize_handles_illegal_reserved_and_hostile_names() {
    assert_eq!(sanitize_segment("Acme: Trading / Muscat?"), "Acme- Trading - Muscat");
    assert_eq!(sanitize_segment("CON"), "CON_");
    assert_eq!(sanitize_segment("nul.txt"), "nul_.txt");
    assert_eq!(sanitize_segment("lpt9.log"), "lpt9_.log");
    assert_eq!(sanitize_segment("com1"), "com1_");
    assert_eq!(sanitize_segment("..\\..\\Windows"), "Windows");
    assert_eq!(sanitize_segment("../../etc/passwd"), "etc-passwd");
    assert_eq!(sanitize_segment("  trailing dots... "), "trailing dots");
    assert_eq!(sanitize_segment("..."), "Company");
    assert_eq!(sanitize_segment(""), "Company");
    assert_eq!(sanitize_segment("   "), "Company");
    assert_eq!(sanitize_segment("شركة النور للمقاولات"), "شركة النور للمقاولات");
    assert_eq!(sanitize_segment("tab\there\nnewline"), "tab-here-newline");
    assert!(sanitize_segment(&"x".repeat(500)).chars().count() <= 80);
}

#[test]
fn sanitize_is_total_and_never_yields_unsafe_segments() {
    let nasty = ["", ".", "..", "...", "a/b", "C:\\x", "AUX", "lpt9.log", "\u{0}", "a\u{7}b", " . ", "<>|?*", "CON ", "name.", "é\u{301}"];
    for n in nasty {
        let s = sanitize_segment(n);
        assert!(!s.is_empty(), "{n:?}");
        assert!(!s.contains(['/', '\\', ':', '*', '?', '"', '<', '>', '|']) && !s.chars().any(|c| c.is_control()), "{n:?} -> {s:?}");
        assert!(s != "." && s != ".." && !s.ends_with('.') && !s.ends_with(' '), "{n:?} -> {s:?}");
        let stem = s.split('.').next().unwrap().to_ascii_uppercase();
        assert!(!["CON", "PRN", "AUX", "NUL", "COM1", "LPT1", "LPT9"].contains(&stem.as_str()), "{n:?} -> {s:?}");
        assert_eq!(sanitize_segment(&s), s, "idempotent for {n:?}");
    }
}

#[test]
fn company_directory_names_are_unique_and_case_insensitive() {
    let taken = |n: &str| n.eq_ignore_ascii_case("Acme");
    let n = company_dir_name("ACME", "01a1b2c3-d4e5-f607-8899-aabbccddeeff", &taken);
    assert!(n.starts_with("ACME (") && n.ends_with(')'));
    assert_eq!(company_dir_name("Other", "id1", &taken), "Other");
}

#[test]
fn root_validation_accepts_a_writable_directory_and_reports_space() {
    let d = tempfile::tempdir().unwrap();
    let r = inspect_root(d.path(), false).unwrap();
    assert_eq!(r.status, "ok");
    assert!(r.writable && r.is_dir && r.free_bytes.unwrap() > 0);
    assert_eq!(fs::read_dir(d.path()).unwrap().count(), 0, "the write probe must clean up after itself");
}

#[test]
fn root_validation_rejects_bad_paths() {
    for bad in ["", "relative/dir", "..", "/tmp/../etc", "/"] {
        let e = inspect_root(Path::new(bad), false).unwrap_err();
        assert_eq!(e.code, "invalid_path", "{bad:?}");
    }
    let d = tempfile::tempdir().unwrap();
    assert_eq!(inspect_root(&d.path().join("missing"), false).unwrap().status, "missing");
    let file = d.path().join("f.txt");
    fs::write(&file, "x").unwrap();
    assert_eq!(inspect_root(&file, false).unwrap().status, "not_a_directory");
    assert_eq!(validate_new_root(&d.path().join("missing"), None).unwrap_err().code, "path_missing");
    assert_eq!(validate_new_root(&file, None).unwrap_err().code, "not_a_directory");
}

#[test]
fn creating_a_root_needs_an_existing_parent() {
    let d = tempfile::tempdir().unwrap();
    let ok = inspect_root(&d.path().join("Artify"), true).unwrap();
    assert_eq!(ok.status, "ok");
    assert_eq!(inspect_root(&d.path().join("a/b/c"), true).unwrap_err().code, "path_missing");
}

#[cfg(target_os = "linux")]
#[test]
fn a_location_the_app_may_not_write_to_is_refused() {
    // /proc is never writable, even for root, so this holds in every CI user context
    let r = inspect_root(Path::new("/proc/self"), false).unwrap();
    assert_eq!(r.status, "not_writable");
    assert_eq!(validate_new_root(Path::new("/proc/self"), None).unwrap_err().code, "permission_denied");
    assert_eq!(ensure_company_dir(Path::new("/proc/self"), "Acme").unwrap_err().code, "permission_denied");
}

#[test]
fn a_new_root_may_not_overlap_the_old_one() {
    let d = tempfile::tempdir().unwrap();
    let old = d.path().join("old");
    let nested = old.join("inner");
    let sibling = d.path().join("new");
    for p in [&old, &nested, &sibling] {
        fs::create_dir_all(p).unwrap();
    }
    assert_eq!(validate_new_root(&nested, Some(&old)).unwrap_err().code, "overlapping_root");
    assert_eq!(validate_new_root(&old, Some(&nested)).unwrap_err().code, "overlapping_root");
    assert_eq!(validate_new_root(&old, Some(&old)).unwrap_err().code, "overlapping_root");
    assert!(validate_new_root(&sibling, Some(&old)).is_ok());
}

#[test]
fn company_directories_are_created_idempotently_and_never_overwrite_files() {
    let d = tempfile::tempdir().unwrap();
    let p1 = ensure_company_dir(d.path(), "Company A").unwrap();
    fs::write(p1.join("keep.txt"), "mine").unwrap();
    let p2 = ensure_company_dir(d.path(), "Company A").unwrap();
    assert_eq!(p1, p2);
    assert_eq!(fs::read_to_string(p2.join("keep.txt")).unwrap(), "mine");
    let b = ensure_company_dir(d.path(), "Company B").unwrap();
    assert_ne!(p1, b);
    assert!(!b.join("keep.txt").exists(), "company data stays separated");
    fs::write(d.path().join("Blocker"), "file").unwrap();
    assert_eq!(ensure_company_dir(d.path(), "Blocker").unwrap_err().code, "already_exists");
    assert_eq!(fs::read_to_string(d.path().join("Blocker")).unwrap(), "file");
}

#[test]
fn company_directory_names_that_are_not_safe_segments_are_refused() {
    let d = tempfile::tempdir().unwrap();
    for bad in ["../escape", "a/b", "..", "", "CON", "x:y"] {
        assert_eq!(ensure_company_dir(d.path(), bad).unwrap_err().code, "invalid_path", "{bad:?}");
    }
    assert!(!d.path().parent().unwrap().join("escape").exists());
}

#[test]
fn deep_storage_locations_are_refused_before_documents_could_overflow_windows_limits() {
    let d = tempfile::tempdir().unwrap();
    let mut deep = d.path().to_path_buf();
    for _ in 0..12 {
        deep.push("a_long_folder_name_segment");
    }
    fs::create_dir_all(&deep).unwrap();
    assert_eq!(ensure_company_dir(&deep, "Company").unwrap_err().code, "path_too_long");
}

#[cfg(unix)]
#[test]
fn a_symlink_cannot_smuggle_a_company_directory_outside_the_root() {
    let d = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let root = d.path().join("root");
    fs::create_dir(&root).unwrap();
    std::os::unix::fs::symlink(outside.path(), root.join("Linked")).unwrap();
    assert_eq!(ensure_company_dir(&root, "Linked").unwrap_err().code, "path_escape");
}

#[test]
fn a_disconnected_storage_location_is_reported_not_recreated() {
    let d = tempfile::tempdir().unwrap();
    let root = d.path().join("USB");
    fs::create_dir(&root).unwrap();
    fs::remove_dir(&root).unwrap();
    assert_eq!(ensure_company_dir(&root, "Acme").unwrap_err().code, "storage_unavailable");
    assert!(!root.exists(), "an unplugged drive's folder must not be silently recreated");
}

#[test]
fn redaction_removes_credentials_from_text() {
    for (input, secret) in [
        ("Authorization: Bearer abc.def.ghi trailing", "abc.def.ghi"),
        (r#"{"password":"hunter2","email":"a@b.c"}"#, "hunter2"),
        (r#"{"refresh_token": "r3fr3sh"}"#, "r3fr3sh"),
        ("access_token=eyJhbGciOi&x=1", "eyJhbGciOi"),
        (r#"{"mfa_token":"signed.challenge"}"#, "signed.challenge"),
        ("client_secret = 'topsecret'", "topsecret"),
    ] {
        let out = redact(input);
        assert!(!out.contains(secret), "{input:?} -> {out:?}");
        assert!(out.contains("[redacted]"));
    }
    assert_eq!(redact("Workspace updated: 2 renamed."), "Workspace updated: 2 renamed.");
    assert_eq!(redact("شركة النور"), "شركة النور");
}

#[test]
fn the_logger_writes_json_lines_and_never_the_secret() {
    let d = tempfile::tempdir().unwrap();
    let log = Logger::new(d.path()).unwrap();
    log.info("sign_in", "ok");
    log.error("oops", "request failed with Bearer SUPERSECRETTOKEN and password=pw123");
    let text = fs::read_to_string(d.path().join("artify-desktop.log")).unwrap();
    assert!(!text.contains("SUPERSECRETTOKEN") && !text.contains("pw123"));
    for line in text.lines() {
        let v: serde_json::Value = serde_json::from_str(line).expect("each line is JSON");
        assert!(v["ts"].is_string() && v["level"].is_string() && v["event"].is_string());
    }
    assert_eq!(log.tail(1).len(), 1);
}
