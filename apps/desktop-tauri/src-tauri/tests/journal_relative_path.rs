//! T08: a journal opened through a relative path reopens through the same path.
//! Separate test binary: it changes the process working directory.
use vox_agent_desktop_tauri_lib::context_journal::{FileAnchorStore, Journal};
use vox_agent_desktop_tauri_lib::context_keys::fake::FakeKeyProvider;
use vox_agent_desktop_tauri_lib::context_keys::KeyRing;

#[test]
fn a_relative_journal_path_reopens() {
    let root = tempfile::tempdir().unwrap();
    std::env::set_current_dir(root.path()).unwrap();
    let open = || {
        let ring = KeyRing::new(std::path::Path::new("keys"), FakeKeyProvider::new([7; 32]));
        let anchors = Box::new(FileAnchorStore::new(std::path::Path::new("anchors")));
        Journal::open(std::path::Path::new("journal"), &ring, anchors)
            .unwrap()
            .into_journal()
    };
    drop(open());
    assert!(open().recovery("A").unwrap().is_empty());
}
