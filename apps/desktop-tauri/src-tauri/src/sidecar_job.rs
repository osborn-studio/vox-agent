//! Process-tree ownership for bundled sidecars on Windows.
//!
//! A PyInstaller one-file executable (the bundled engine and voice backend)
//! is a bootloader that runs the real interpreter as a child process.
//! `Child::kill` on Windows terminates only the bootloader, which would leave
//! the interpreter running, holding its port and database files. Each sidecar
//! is therefore placed in its own job object that kills every process in it
//! when the job handle closes: on an explicit stop, and also when the desktop
//! process exits or crashes, because Windows closes the handle then.
//!
//! The child is assigned right after spawn, before the bootloader has
//! extracted its payload and started the interpreter, so the interpreter
//! inherits the job. On other platforms this type is inert.

use std::process::Child;

pub struct SidecarJob {
    #[cfg(windows)]
    handle: usize,
}

impl SidecarJob {
    /// Put `child` (and every process it starts) under a kill-on-close job.
    /// `None` if the job could not be created or assigned.
    #[cfg(windows)]
    pub fn attach(child: &Child) -> Option<Self> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        // SAFETY: plain Win32 calls on handles this function owns; `child`
        // keeps its process handle open for the duration of the call.
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return None;
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let configured = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if configured == 0 || AssignProcessToJobObject(job, child.as_raw_handle() as _) == 0 {
                CloseHandle(job);
                return None;
            }
            Some(Self {
                handle: job as usize,
            })
        }
    }

    #[cfg(not(windows))]
    pub fn attach(_child: &Child) -> Option<Self> {
        Some(Self {})
    }
}

impl Drop for SidecarJob {
    fn drop(&mut self) {
        #[cfg(windows)]
        {
            // SAFETY: the handle came from CreateJobObjectW and is closed once.
            // KILL_ON_JOB_CLOSE ends every process still in the job.
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.handle as _);
            }
        }
    }
}
