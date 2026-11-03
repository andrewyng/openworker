//! Optional guardrail engine and persistent configuration contract.
pub use openworker_guardrails::*;
impl From<crate::connections::Choice> for ModelRef {
    fn from(value: crate::connections::Choice) -> Self {
        Self {
            connection: value.connection,
            model: value.model,
        }
    }
}
impl From<ModelRef> for crate::connections::Choice {
    fn from(value: ModelRef) -> Self {
        Self {
            connection: value.connection,
            model: value.model,
        }
    }
}
