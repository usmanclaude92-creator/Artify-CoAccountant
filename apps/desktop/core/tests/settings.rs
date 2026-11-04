use artify_core::settings::validate;

#[test]
fn only_known_settings_with_valid_values_are_accepted() {
    for (k, v) in [
        ("theme", "dark"),
        ("theme", "system"),
        ("language", "ar"),
        ("notifications", "false"),
        ("update_channel", "beta"),
        ("default_company_id", ""),
        ("default_company_id", "01a1b2c3-d4e5"),
        ("storage_root", "D:\\Artify"),
    ] {
        assert!(validate(k, v).is_ok(), "{k}={v}");
    }
    for (k, v) in [
        ("theme", "neon"),
        ("language", "xx"),
        ("notifications", "yes"),
        ("update_channel", "nightly"),
        ("default_company_id", "../../etc"),
        ("default_company_id", "'; DROP TABLE x;--"),
        ("storage_root", "a\0b"),
    ] {
        assert_eq!(validate(k, v).unwrap_err().code, "invalid_setting", "{k}={v}");
    }
    for k in ["refresh_token", "password", "api_base", ""] {
        assert_eq!(validate(k, "x").unwrap_err().code, "unknown_setting", "{k}");
    }
}
