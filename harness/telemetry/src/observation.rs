//! Only bounded numeric measurements, never caller-provided names or labels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(usize)]
pub enum Gauge {
    SessionsActive,
    TurnsQueued,
    ApprovalsPending,
    QuestionsPending,
    OldestQueuedSeconds,
    SessionsStalled,
    UnknownRecent,
    StateObservedAt,
    DatabaseHealthy,
    WorkerCapacity,
    WorkerActive,
    WorkerHeartbeat,
    SamplerHeartbeat,
    WorkerDraining,
}
impl Gauge {
    pub const ALL: [Self; 14] = [
        Self::SessionsActive,
        Self::TurnsQueued,
        Self::ApprovalsPending,
        Self::QuestionsPending,
        Self::OldestQueuedSeconds,
        Self::SessionsStalled,
        Self::UnknownRecent,
        Self::StateObservedAt,
        Self::DatabaseHealthy,
        Self::WorkerCapacity,
        Self::WorkerActive,
        Self::WorkerHeartbeat,
        Self::SamplerHeartbeat,
        Self::WorkerDraining,
    ];
    pub fn name(self) -> &'static str {
        match self {
            Self::SessionsActive => "openworker.server.sessions_active",
            Self::TurnsQueued => "openworker.server.turns_queued",
            Self::ApprovalsPending => "openworker.server.approvals_pending",
            Self::QuestionsPending => "openworker.server.questions_pending",
            Self::OldestQueuedSeconds => "openworker.server.oldest_queued_seconds",
            Self::SessionsStalled => "openworker.server.sessions_stalled",
            Self::UnknownRecent => "openworker.server.unknown_recent",
            Self::StateObservedAt => "openworker.server.state_observed_timestamp_seconds",
            Self::DatabaseHealthy => "openworker.server.database_healthy",
            Self::WorkerCapacity => "openworker.worker.capacity",
            Self::WorkerActive => "openworker.worker.active",
            Self::WorkerHeartbeat => "openworker.worker.heartbeat_timestamp_seconds",
            Self::SamplerHeartbeat => "openworker.server.sampler_heartbeat_timestamp_seconds",
            Self::WorkerDraining => "openworker.worker.draining",
        }
    }
}
#[derive(Clone, Copy, Debug)]
pub struct Observation {
    pub gauge: Gauge,
    pub value: u64,
}
