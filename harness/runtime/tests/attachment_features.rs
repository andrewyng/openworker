use openworker_runtime::{
    attachments::*,
    store::{StateStore, Store},
    turn_options::Context,
};
#[test]
fn attachment_capability_tracks_the_build_feature() {
    let mut store = Store::memory().unwrap();
    assert_eq!(
        store.supports_input_attachments(),
        cfg!(feature = "attachments")
    );
    let metadata = Metadata {
        id: "a".into(),
        name: "a.txt".into(),
        media_type: "text/plain".into(),
        size: 1,
        sha256: openworker_runtime::bundle::digest(b"a"),
    };
    assert_eq!(metadata.validate().is_ok(), cfg!(feature = "attachments"));
    let context = Context {
        content: Some(vec![Part::Text {
            text: "hello".into(),
        }]),
        ..Default::default()
    };
    assert_eq!(context.validate().is_ok(), cfg!(feature = "attachments"));
    if !cfg!(feature = "attachments") {
        assert!(store
            .input_attachment("missing", "a", Upload::Begin { metadata })
            .is_err());
    }
}
