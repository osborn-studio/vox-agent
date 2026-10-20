//! Owner-only directories for the Context journal on Windows (spec §3.5).
//!
//! `ensure_private_dir` creates a missing directory with a protected DACL
//! that grants full access only to the current user and SYSTEM, with no
//! inheritance from the parent. `verify_private_dir` re-reads the DACL and
//! fails on any other allow ACE, on any inherited ACE, on a NULL DACL, on a
//! foreign owner and on a reparse point, so a directory someone widened
//! later (or that merely inherits its parent's permissions) is not used. An
//! existing directory is verified, never repaired. On non-Windows targets
//! only the reparse-point check applies, because those builds never open a
//! journal (see `context_commands::open_journal`).
use std::path::Path;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AclError {
    /// Could not read the current user's SID, read or apply the descriptor,
    /// or the directory does not exist.
    Io,
    /// The DACL is NULL, inherits from the parent, or holds an allow ACE for a
    /// principal other than the owner or SYSTEM; the owner is a foreign
    /// principal; or the path is a symlink, junction or other reparse point.
    TooPermissive,
}

/// A symlink, junction or other reparse point is never a private directory:
/// its target could be anywhere and the ACL read here would not be its own.
fn is_reparse_point(dir: &Path) -> bool {
    let Ok(metadata) = std::fs::symlink_metadata(dir) else {
        return false;
    };
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

#[cfg(not(windows))]
pub fn ensure_private_dir(dir: &Path) -> Result<(), AclError> {
    if is_reparse_point(dir) {
        return Err(AclError::TooPermissive);
    }
    std::fs::create_dir_all(dir).map_err(|_| AclError::Io)
}

#[cfg(not(windows))]
pub fn verify_private_dir(dir: &Path) -> Result<(), AclError> {
    if is_reparse_point(dir) {
        return Err(AclError::TooPermissive);
    }
    if dir.is_dir() {
        Ok(())
    } else {
        Err(AclError::Io)
    }
}

#[cfg(windows)]
mod win {
    use super::{is_reparse_point, AclError};
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;
    use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, HANDLE};
    use windows_sys::Win32::Security::Authorization::{
        ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
        GetNamedSecurityInfoW, SetNamedSecurityInfoW, SDDL_REVISION_1, SE_FILE_OBJECT,
    };
    use windows_sys::Win32::Security::{
        GetAce, GetSecurityDescriptorDacl, GetTokenInformation, IsValidSid, TokenUser,
        ACCESS_ALLOWED_ACE, ACE_HEADER, ACL, DACL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION,
        PROTECTED_DACL_SECURITY_INFORMATION, TOKEN_QUERY, TOKEN_USER,
    };
    // windows-sys 0.61: the ACE type constants are plain u32 values here.
    use windows_sys::Win32::System::SystemServices::{
        ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

    const SYSTEM_SID: &str = "S-1-5-18";
    /// Objects created by an elevated process are owned by this group.
    const ADMINISTRATORS_SID: &str = "S-1-5-32-544";
    /// `ACE_HEADER.AceFlags` bit set on ACEs inherited from the parent.
    const INHERITED_ACE: u8 = 0x10;

    fn wide(s: &OsStr) -> Vec<u16> {
        s.encode_wide().chain(std::iter::once(0)).collect()
    }

    /// String form (`S-1-5-...`) of a SID.
    ///
    /// # Safety
    /// `sid` must point at a SID that stays valid for the call.
    unsafe fn sid_to_string(sid: *mut core::ffi::c_void) -> Result<String, AclError> {
        if sid.is_null() || IsValidSid(sid) == 0 {
            return Err(AclError::Io);
        }
        let mut out: *mut u16 = std::ptr::null_mut();
        if ConvertSidToStringSidW(sid, &mut out) == 0 || out.is_null() {
            return Err(AclError::Io);
        }
        let mut len = 0;
        while *out.add(len) != 0 {
            len += 1;
        }
        let text = String::from_utf16_lossy(std::slice::from_raw_parts(out, len));
        LocalFree(out as _);
        Ok(text)
    }

    /// String SID of the user running this process.
    pub fn current_user_sid() -> Result<String, AclError> {
        let mut token: HANDLE = std::ptr::null_mut();
        // SAFETY: standard query of the current process token; the handle is
        // closed below on every path after the open succeeded.
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(AclError::Io);
        }
        let mut needed = 0u32;
        // SAFETY: a zero-length query only reports the required size.
        unsafe { GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut needed) };
        if needed == 0 {
            unsafe { CloseHandle(token) };
            return Err(AclError::Io);
        }
        // Over-align the buffer so `TOKEN_USER` can be read from it.
        let mut buffer = vec![0u64; (needed as usize).div_ceil(8)];
        // SAFETY: the buffer holds `needed` bytes; the SID the result points
        // at lives inside the same buffer, which outlives `sid_to_string`.
        let ok = unsafe {
            GetTokenInformation(
                token,
                TokenUser,
                buffer.as_mut_ptr() as _,
                needed,
                &mut needed,
            )
        };
        unsafe { CloseHandle(token) };
        if ok == 0 {
            return Err(AclError::Io);
        }
        let user = unsafe { &*(buffer.as_ptr() as *const TOKEN_USER) };
        unsafe { sid_to_string(user.User.Sid) }
    }

    /// A self-relative security descriptor built from SDDL; freed on drop.
    struct Descriptor(*mut core::ffi::c_void);

    impl Descriptor {
        fn from_sddl(sddl: &str) -> Result<Self, AclError> {
            let sddl_w = wide(OsStr::new(sddl));
            let mut descriptor: *mut core::ffi::c_void = std::ptr::null_mut();
            // SAFETY: the SDDL string is NUL-terminated; the descriptor is
            // LocalAlloc'd on success and freed by `Drop`.
            let ok = unsafe {
                ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl_w.as_ptr(),
                    SDDL_REVISION_1,
                    &mut descriptor,
                    std::ptr::null_mut(),
                )
            };
            if ok == 0 || descriptor.is_null() {
                return Err(AclError::Io);
            }
            Ok(Self(descriptor))
        }
        /// The DACL inside the descriptor (valid while `self` lives).
        fn dacl(&self) -> Result<*mut ACL, AclError> {
            let mut dacl: *mut ACL = std::ptr::null_mut();
            let mut present = 0i32;
            let mut defaulted = 0i32;
            // SAFETY: `self.0` is a valid self-relative descriptor.
            let ok = unsafe {
                GetSecurityDescriptorDacl(self.0, &mut present, &mut dacl, &mut defaulted)
            };
            if ok == 0 || present == 0 || dacl.is_null() {
                return Err(AclError::Io);
            }
            Ok(dacl)
        }
    }

    impl Drop for Descriptor {
        fn drop(&mut self) {
            unsafe { LocalFree(self.0 as _) };
        }
    }

    /// Creates a missing directory with the protected DACL. An existing
    /// directory is verified, never repaired: silently tightening an ACL
    /// someone widened would hide the tampering the check exists to catch.
    /// Everything that can fail is prepared before the directory exists, and
    /// a directory this call created but could not secure is removed again,
    /// so a failure never leaves an inherited, permanently refused directory.
    pub fn ensure_private_dir(dir: &Path) -> Result<(), AclError> {
        if is_reparse_point(dir) {
            return Err(AclError::TooPermissive);
        }
        if dir.is_dir() {
            return verify_private_dir(dir);
        }
        let user = current_user_sid()?;
        // Protected DACL (no inheritance), full access for SYSTEM and the
        // owner, inherited by children (OICI).
        let descriptor = Descriptor::from_sddl(&format!(
            "D:P(A;OICI;FA;;;{SYSTEM_SID})(A;OICI;FA;;;{user})"
        ))?;
        let dacl = descriptor.dacl()?;
        if let Some(parent) = dir.parent() {
            std::fs::create_dir_all(parent).map_err(|_| AclError::Io)?;
        }
        // Only the leaf is created here; a directory that appeared meanwhile
        // was not created by us and is verified like any other.
        match std::fs::create_dir(dir) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                return verify_private_dir(dir);
            }
            Err(_) => return Err(AclError::Io),
        }
        let path_w = wide(dir.as_os_str());
        // SAFETY: path is NUL-terminated; owner and group are left unchanged;
        // `dacl` lives inside `descriptor`, which outlives the call.
        let status = unsafe {
            SetNamedSecurityInfoW(
                path_w.as_ptr(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                dacl,
                std::ptr::null(),
            )
        };
        drop(descriptor);
        let secured = if status == 0 {
            verify_private_dir(dir)
        } else {
            Err(AclError::Io)
        };
        if secured.is_err() {
            // Remove the empty directory this call just created (never one
            // that existed before), so the next start can create it properly.
            let _ = std::fs::remove_dir(dir);
        }
        secured
    }

    /// Re-read owner and DACL: the owner must be the current user, SYSTEM or
    /// Administrators (an elevated process creates objects owned by that
    /// group), and only non-inherited allow ACEs for the current user and
    /// SYSTEM may exist (deny ACEs only narrow access and are ignored).
    pub fn verify_private_dir(dir: &Path) -> Result<(), AclError> {
        if is_reparse_point(dir) {
            return Err(AclError::TooPermissive);
        }
        if !dir.is_dir() {
            return Err(AclError::Io);
        }
        let user = current_user_sid()?;
        let path_w = wide(dir.as_os_str());
        let mut owner: *mut core::ffi::c_void = std::ptr::null_mut();
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut descriptor: *mut core::ffi::c_void = std::ptr::null_mut();
        // SAFETY: out-params are filled on success; owner and DACL point into
        // the descriptor, which is freed after the walk.
        let status = unsafe {
            GetNamedSecurityInfoW(
                path_w.as_ptr(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                std::ptr::null_mut(),
                &mut dacl,
                std::ptr::null_mut(),
                &mut descriptor,
            )
        };
        if status != 0 || descriptor.is_null() {
            return Err(AclError::Io);
        }
        let result = (|| {
            // SAFETY: `owner` points into the live descriptor.
            let owner = unsafe { sid_to_string(owner) }?;
            if owner != user && owner != SYSTEM_SID && owner != ADMINISTRATORS_SID {
                // Another owner holds implicit WRITE_DAC and could widen it later.
                return Err(AclError::TooPermissive);
            }
            walk_dacl(dacl, &user)
        })();
        unsafe { LocalFree(descriptor as _) };
        result
    }

    fn walk_dacl(dacl: *mut ACL, user: &str) -> Result<(), AclError> {
        if dacl.is_null() {
            // A NULL DACL grants everyone full access.
            return Err(AclError::TooPermissive);
        }
        // SAFETY: `dacl` points into a descriptor that outlives this walk.
        let count = u32::from(unsafe { (*dacl).AceCount });
        for index in 0..count {
            let mut ace: *mut core::ffi::c_void = std::ptr::null_mut();
            if unsafe { GetAce(dacl, index, &mut ace) } == 0 || ace.is_null() {
                return Err(AclError::Io);
            }
            let header = unsafe { &*(ace as *const ACE_HEADER) };
            if header.AceFlags & INHERITED_ACE != 0 {
                // Inherited permissions change with the parent; the journal
                // directory must carry its own protected DACL.
                return Err(AclError::TooPermissive);
            }
            let ace_type = u32::from(header.AceType);
            if ace_type == ACCESS_DENIED_ACE_TYPE {
                continue;
            }
            if ace_type != ACCESS_ALLOWED_ACE_TYPE {
                // Object, callback and audit ACE forms are not evaluated here;
                // refuse rather than guess.
                return Err(AclError::TooPermissive);
            }
            // The SID starts at `SidStart` and runs past the fixed struct, so
            // it is addressed from the raw ACE pointer, not through a
            // reference to the 12-byte struct.
            let sid = unsafe {
                (ace as *const u8).add(core::mem::offset_of!(ACCESS_ALLOWED_ACE, SidStart))
            } as *mut core::ffi::c_void;
            let sid = unsafe { sid_to_string(sid) }?;
            if sid != user && sid != SYSTEM_SID {
                return Err(AclError::TooPermissive);
            }
        }
        Ok(())
    }
}

#[cfg(windows)]
pub use win::{current_user_sid, ensure_private_dir, verify_private_dir};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ensure_creates_and_verify_accepts_a_fresh_directory() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("ctx");
        ensure_private_dir(&dir).unwrap();
        assert!(dir.is_dir());
        ensure_private_dir(&dir).unwrap();
        verify_private_dir(&dir).unwrap();
        assert_eq!(
            verify_private_dir(&root.path().join("absent")).unwrap_err(),
            AclError::Io
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_symlink_is_never_a_private_directory() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("target");
        std::fs::create_dir(&target).unwrap();
        let link = root.path().join("link");
        std::os::unix::fs::symlink(&target, &link).unwrap();
        assert_eq!(
            verify_private_dir(&link).unwrap_err(),
            AclError::TooPermissive
        );
        assert_eq!(
            ensure_private_dir(&link).unwrap_err(),
            AclError::TooPermissive
        );
    }
}
