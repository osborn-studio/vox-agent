//! One owner for native microphone capture (T26).
//!
//! Every capture is a session with a number and an owner (the global hotkey
//! or the window UI). Only the session's own number can stop or cancel it, so
//! a late "released" event or a stale button press can never end, or collect
//! the audio of, a capture that someone else started. A second start while
//! one runs is refused as busy; it is not a reason to open another recorder.
//! When the device fails (unplugged, switched), the stream and its samples
//! are discarded at once instead of being kept open.

/// The audio source behind a session (a cpal stream in production).
pub trait CaptureHandle {
    /// The device reported an error; the capture cannot be trusted.
    fn failed(&self) -> bool;
    /// Stop the stream and wipe the captured samples. Nothing is returned.
    fn discard(self);
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum CaptureOwner {
    Hotkey,
    Ui,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionError {
    /// Another session is running; carries its owner.
    Busy(CaptureOwner),
    NotRunning,
    /// The number does not belong to the running session.
    StaleSession,
    /// The device failed; the session was discarded.
    DeviceLost,
    /// The device could not be opened.
    Open(String),
}

impl SessionError {
    /// Stable code for the WebView; never contains audio or device data.
    pub fn code(&self) -> String {
        match self {
            Self::Busy(CaptureOwner::Hotkey) => "capture-busy:hotkey".into(),
            Self::Busy(CaptureOwner::Ui) => "capture-busy:ui".into(),
            Self::NotRunning => "capture-not-running".into(),
            Self::StaleSession => "capture-stale-session".into(),
            Self::DeviceLost => "capture-device-lost".into(),
            Self::Open(message) => format!("capture-open-failed: {message}"),
        }
    }
}

struct Active<C> {
    id: u64,
    owner: CaptureOwner,
    handle: C,
}

pub struct CaptureSessions<C: CaptureHandle> {
    active: Option<Active<C>>,
    next_id: u64,
}

impl<C: CaptureHandle> Default for CaptureSessions<C> {
    fn default() -> Self {
        Self {
            active: None,
            next_id: 1,
        }
    }
}

impl<C: CaptureHandle> CaptureSessions<C> {
    /// Start a session for `owner`, opening the device only when idle.
    pub fn begin(
        &mut self,
        owner: CaptureOwner,
        open: impl FnOnce() -> Result<C, String>,
    ) -> Result<u64, SessionError> {
        self.reap_failed();
        if let Some(active) = &self.active {
            return Err(SessionError::Busy(active.owner));
        }
        let handle = open().map_err(SessionError::Open)?;
        let id = self.next_id;
        self.next_id = self.next_id.wrapping_add(1).max(1);
        self.active = Some(Active { id, owner, handle });
        Ok(id)
    }

    /// The running session, if any (after discarding a failed one).
    pub fn current(&mut self) -> Option<(u64, CaptureOwner, &C)> {
        self.reap_failed();
        self.active.as_ref().map(|a| (a.id, a.owner, &a.handle))
    }

    /// Hand session `id`'s capture to the caller to finalize. A failed device
    /// is discarded and reported instead.
    pub fn finish(&mut self, id: u64) -> Result<C, SessionError> {
        let active = self.take(id)?;
        if active.handle.failed() {
            active.handle.discard();
            return Err(SessionError::DeviceLost);
        }
        Ok(active.handle)
    }

    /// Cancel session `id`: the stream stops and the samples are wiped here.
    pub fn cancel(&mut self, id: u64) -> Result<(), SessionError> {
        self.take(id)?.handle.discard();
        Ok(())
    }

    /// Discard a session whose device failed. Returns whether one was.
    pub fn reap_failed(&mut self) -> bool {
        if self.active.as_ref().is_some_and(|a| a.handle.failed()) {
            if let Some(active) = self.active.take() {
                active.handle.discard();
            }
            return true;
        }
        false
    }

    fn take(&mut self, id: u64) -> Result<Active<C>, SessionError> {
        match &self.active {
            None => Err(SessionError::NotRunning),
            Some(active) if active.id != id => Err(SessionError::StaleSession),
            Some(_) => Ok(self.active.take().expect("checked above")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::rc::Rc;

    #[derive(Default)]
    struct Probe {
        opened: Cell<u32>,
        discarded: Cell<u32>,
        failed: Cell<bool>,
        log: RefCell<Vec<&'static str>>,
    }

    struct Fake(Rc<Probe>);

    impl CaptureHandle for Fake {
        fn failed(&self) -> bool {
            self.0.failed.get()
        }
        fn discard(self) {
            self.0.discarded.set(self.0.discarded.get() + 1);
            self.0.log.borrow_mut().push("discard");
        }
    }

    fn open(probe: &Rc<Probe>) -> impl FnOnce() -> Result<Fake, String> + '_ {
        move || {
            probe.opened.set(probe.opened.get() + 1);
            Ok(Fake(Rc::clone(probe)))
        }
    }

    #[test]
    fn a_second_start_is_busy_and_never_opens_another_device() {
        let probe = Rc::new(Probe::default());
        let mut sessions = CaptureSessions::default();
        let id = sessions.begin(CaptureOwner::Hotkey, open(&probe)).unwrap();
        assert_eq!(
            sessions.begin(CaptureOwner::Ui, open(&probe)).err(),
            Some(SessionError::Busy(CaptureOwner::Hotkey))
        );
        assert_eq!(probe.opened.get(), 1);
        assert_eq!(
            sessions.current().map(|(i, o, _)| (i, o)),
            Some((id, CaptureOwner::Hotkey))
        );
    }

    #[test]
    fn only_the_sessions_own_number_can_stop_or_cancel_it() {
        let probe = Rc::new(Probe::default());
        let mut sessions = CaptureSessions::default();
        let first = sessions.begin(CaptureOwner::Ui, open(&probe)).unwrap();
        sessions.cancel(first).unwrap();
        let second = sessions.begin(CaptureOwner::Hotkey, open(&probe)).unwrap();
        assert_ne!(first, second);
        // A late release from the first session must not touch the second.
        assert_eq!(
            sessions.finish(first).err(),
            Some(SessionError::StaleSession)
        );
        assert_eq!(
            sessions.cancel(first).err(),
            Some(SessionError::StaleSession)
        );
        assert!(sessions.current().is_some());
        assert!(sessions.finish(second).is_ok());
        assert_eq!(
            sessions.finish(second).err(),
            Some(SessionError::NotRunning)
        );
    }

    #[test]
    fn cancel_discards_the_audio_inside_the_capture_owner() {
        let probe = Rc::new(Probe::default());
        let mut sessions = CaptureSessions::default();
        let id = sessions.begin(CaptureOwner::Ui, open(&probe)).unwrap();
        sessions.cancel(id).unwrap();
        assert_eq!(probe.discarded.get(), 1);
        assert!(sessions.current().is_none());
    }

    #[test]
    fn a_failed_device_is_discarded_and_never_finalized() {
        let probe = Rc::new(Probe::default());
        let mut sessions = CaptureSessions::default();
        let id = sessions.begin(CaptureOwner::Hotkey, open(&probe)).unwrap();
        probe.failed.set(true);
        assert_eq!(sessions.finish(id).err(), Some(SessionError::DeviceLost));
        assert_eq!(probe.discarded.get(), 1);

        // Discovered by a status poll instead: the stream is released then.
        probe.failed.set(false);
        let _ = sessions.begin(CaptureOwner::Ui, open(&probe)).unwrap();
        probe.failed.set(true);
        assert!(sessions.current().is_none());
        assert_eq!(probe.discarded.get(), 2);
        probe.failed.set(false);
        assert!(
            sessions.begin(CaptureOwner::Ui, open(&probe)).is_ok(),
            "a new capture can start"
        );
    }

    #[test]
    fn an_open_failure_leaves_no_session() {
        let mut sessions: CaptureSessions<Fake> = CaptureSessions::default();
        assert_eq!(
            sessions
                .begin(CaptureOwner::Ui, || Err("no device".into()))
                .err(),
            Some(SessionError::Open("no device".into()))
        );
        assert!(sessions.current().is_none());
    }

    #[test]
    fn error_codes_are_stable() {
        assert_eq!(
            SessionError::Busy(CaptureOwner::Hotkey).code(),
            "capture-busy:hotkey"
        );
        assert_eq!(SessionError::StaleSession.code(), "capture-stale-session");
        assert_eq!(SessionError::DeviceLost.code(), "capture-device-lost");
    }
}
