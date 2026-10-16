use serde::Serialize;

const MAX_INJECTION_CHARACTERS: usize = 20_000;

struct PreparedText<'a> {
    text: &'a str,
    character_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TextInjectionResult {
    pub method: String,
    pub character_count: usize,
    pub clipboard_updated: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClipboardCopyResult {
    pub character_count: usize,
    pub clipboard_updated: bool,
}

fn prepare_text(text: &str) -> Result<PreparedText<'_>, String> {
    if text.trim().is_empty() {
        return Err("text injection payload must not be empty".to_string());
    }
    let character_count = text.chars().count();
    if character_count > MAX_INJECTION_CHARACTERS {
        return Err(format!(
            "text injection payload exceeds the {MAX_INJECTION_CHARACTERS} character limit"
        ));
    }
    Ok(PreparedText {
        text,
        character_count,
    })
}

#[cfg(any(windows, test))]
fn unicode_units(text: &str) -> Vec<u16> {
    text.encode_utf16().collect()
}

pub fn inject_text(text: &str, requested_method: &str) -> Result<TextInjectionResult, String> {
    let prepared = prepare_text(text)?;
    let requested = requested_method.trim().to_ascii_lowercase();
    if !matches!(requested.as_str(), "direct_typing" | "paste") {
        return Err("text injection method must be direct_typing or paste".to_string());
    }

    #[cfg(windows)]
    {
        return windows::inject(prepared, &requested);
    }
    #[cfg(not(windows))]
    {
        let _ = prepared.text;
        let _ = prepared.character_count;
        let _ = requested;
        Err("native text injection is only available in the Windows desktop build".to_string())
    }
}

pub fn copy_text(text: &str) -> Result<ClipboardCopyResult, String> {
    let prepared = prepare_text(text)?;

    #[cfg(windows)]
    {
        let mut clipboard = arboard::Clipboard::new()
            .map_err(|error| format!("failed to open the Windows clipboard: {error}"))?;
        clipboard
            .set_text(prepared.text.to_string())
            .map_err(|error| format!("failed to copy text to the Windows clipboard: {error}"))?;
        return Ok(ClipboardCopyResult {
            character_count: prepared.character_count,
            clipboard_updated: true,
        });
    }
    #[cfg(not(windows))]
    {
        let _ = prepared.text;
        let _ = prepared.character_count;
        Err("native clipboard copy is only available in the Windows desktop build".to_string())
    }
}

#[cfg(windows)]
mod windows {
    use super::{unicode_units, PreparedText, TextInjectionResult};
    use std::mem::size_of;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE,
        VK_CONTROL, VK_V,
    };

    enum DirectOutcome {
        Sent,
        Blocked,
        Partial { sent: u32, expected: u32 },
    }

    pub(super) fn inject(
        prepared: PreparedText<'_>,
        requested: &str,
    ) -> Result<TextInjectionResult, String> {
        if requested == "direct_typing" {
            match send_unicode(prepared.text) {
                DirectOutcome::Sent => {
                    return Ok(TextInjectionResult {
                        method: "direct_typing".to_string(),
                        character_count: prepared.character_count,
                        clipboard_updated: false,
                    });
                }
                DirectOutcome::Partial { sent, expected } => {
                    return Err(format!(
                        "Windows injected only {sent} of {expected} keyboard events; paste fallback was not attempted to avoid duplicate text"
                    ));
                }
                DirectOutcome::Blocked => {}
            }
        }

        paste_from_clipboard(prepared.text)?;
        Ok(TextInjectionResult {
            method: "paste".to_string(),
            character_count: prepared.character_count,
            clipboard_updated: true,
        })
    }

    fn send_unicode(text: &str) -> DirectOutcome {
        let units = unicode_units(text);
        let mut inputs = Vec::with_capacity(units.len() * 2);
        for unit in units {
            inputs.push(keyboard_input(0, unit, KEYEVENTF_UNICODE));
            inputs.push(keyboard_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
        }
        let expected = inputs.len() as u32;
        let sent = unsafe { SendInput(expected, inputs.as_ptr(), size_of::<INPUT>() as i32) };
        if sent == expected {
            DirectOutcome::Sent
        } else if sent == 0 {
            // SendInput can be blocked by User Interface Privilege Isolation when
            // the target app has a higher integrity level. The API intentionally
            // does not report UIPI through GetLastError, so zero is the only safe
            // signal for trying the clipboard fallback.
            DirectOutcome::Blocked
        } else {
            DirectOutcome::Partial { sent, expected }
        }
    }

    fn paste_from_clipboard(text: &str) -> Result<(), String> {
        let mut clipboard = arboard::Clipboard::new()
            .map_err(|error| format!("failed to open the Windows clipboard: {error}"))?;
        clipboard
            .set_text(text.to_string())
            .map_err(|error| format!("failed to copy text to the Windows clipboard: {error}"))?;

        let inputs = [
            keyboard_input(VK_CONTROL, 0, 0),
            keyboard_input(VK_V, 0, 0),
            keyboard_input(VK_V, 0, KEYEVENTF_KEYUP),
            keyboard_input(VK_CONTROL, 0, KEYEVENTF_KEYUP),
        ];
        let expected = inputs.len() as u32;
        let sent = unsafe { SendInput(expected, inputs.as_ptr(), size_of::<INPUT>() as i32) };
        if sent != expected {
            return Err(format!(
                "Windows paste shortcut injected {sent} of {expected} keyboard events; the text remains on the clipboard"
            ));
        }
        Ok(())
    }

    fn keyboard_input(virtual_key: u16, scan_code: u16, flags: u32) -> INPUT {
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: virtual_key,
                    wScan: scan_code,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{prepare_text, unicode_units};

    #[test]
    fn preserves_user_text_and_counts_unicode_scalars() {
        let prepared = prepare_text("  Vox Agent 你好 \u{10437}\n").unwrap();

        assert_eq!(prepared.text, "  Vox Agent 你好 \u{10437}\n");
        assert_eq!(prepared.character_count, 17);
    }

    #[test]
    fn rejects_empty_and_unbounded_injection_payloads() {
        assert!(prepare_text("   \n").is_err());
        assert!(prepare_text(&"a".repeat(20_001)).is_err());
    }

    #[test]
    fn emits_utf16_surrogate_pairs_for_supplementary_characters() {
        assert_eq!(unicode_units("A\u{10437}"), vec![0x0041, 0xD801, 0xDC37]);
    }
}
