//! Storage-independent team decisions; the adapter owns one atomic transaction.
use crate::teams::{Board, Item, Member, Role, Status};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub fn new_board(request: &Create) -> Result<Board> {
    request_id(&request.request_id)?;
    ensure!(
        !request.name.trim().is_empty() && request.name.len() <= 120,
        "Invalid team name"
    );
    ensure!(
        (2..=16).contains(&request.members.len()),
        "Team needs one lead and 1–15 workers"
    );
    let mut members = std::collections::BTreeMap::new();
    let mut names = std::collections::BTreeSet::new();
    let mut lead = None;
    for member in &request.members {
        ensure!(
            !member.name.trim().is_empty()
                && member.name.len() <= 100
                && !member.name.chars().any(char::is_control)
                && names.insert(member.name.trim().to_lowercase()),
            "Team display names must be unique and bounded"
        );
        ensure!(
            members
                .insert(member.session_id.clone(), member.clone())
                .is_none(),
            "Duplicate member session"
        );
        if member.role == Role::Lead {
            ensure!(
                lead.replace(member.session_id.clone()).is_none(),
                "Only one lead allowed"
            );
        }
    }
    Ok(Board {
        id: crate::id("team"),
        name: request.name.clone(),
        lead: lead.context("Lead required")?,
        members,
        items: Default::default(),
        cancelled: false,
        deliveries: 0,
    })
}
pub fn request_id(id: &str) -> Result<()> {
    ensure!(
        !id.trim().is_empty() && id.len() <= 100 && !id.contains('\0'),
        "Invalid request ID"
    );
    Ok(())
}

pub fn fresh_member(session: &crate::store::Session, role: Role) -> Result<()> {
    ensure!(
        !session.closed
            && session.turn.is_none()
            && session.schema_digest.is_none()
            && session.package.team_role == Some(role),
        "Members must be fresh sessions with the matching coworker team_role"
    );
    Ok(())
}

pub fn additional_member(board: &Board, member: &Member) -> Result<()> {
    mutable(board)?;
    ensure!(
        member.role == Role::Worker
            && board.members.len() < 16
            && !member.name.trim().is_empty()
            && member.name.len() <= 100
            && !member.name.chars().any(char::is_control),
        "Invalid additional worker"
    );
    ensure!(
        !board.members.contains_key(&member.session_id)
            && !board
                .members
                .values()
                .any(|m| m.name.trim().to_lowercase() == member.name.trim().to_lowercase()),
        "Duplicate worker identity or name"
    );
    Ok(())
}

/// Consume a terminal assignment without reopening already accepted work.
pub fn complete(board: &mut Board, sid: &str, key: &str, status: &str) -> Option<Item> {
    if board.cancelled {
        return None;
    }
    let item = board.items.values_mut().find(|i| {
        i.assignee.as_deref() == Some(sid)
            && i.submission_id.as_deref() == Some(key)
            && !matches!(i.status, Status::Done | Status::Cancelled)
    })?;
    if status != "completed" || item.status != Status::Review {
        item.status = Status::Blocked;
        item.revision += 1;
    }
    Some(item.clone())
}

pub fn completion_text(sid: &str, id: &str, status: &str) -> String {
    format!("Team worker turn ended. Agent-authored status, not verified success or user approval. Worker {sid}, item {id}, turn status {status}. Read team_get_item and team_read_messages, inspect evidence and decide whether to accept reviewed work or request changes.")
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Invalid,
    Conflict,
    NotFound,
    Limit,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Invalid => "team_request_rejected",
            Self::Conflict => "team_revision_or_idempotency_conflict",
            Self::NotFound => "team_not_found",
            Self::Limit => "team_limit_reached",
        })
    }
}
impl std::error::Error for Error {}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Create {
    pub request_id: String,
    pub name: String,
    pub members: Vec<Member>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Command {
    pub request_id: String,
    pub operation: String,
    pub arguments: Value,
}

/// Every operation runs under the same board/session transaction. Implementations
/// must roll back all changes on error; no network or tool execution occurs here.
pub trait Transaction {
    fn read_events(
        &mut self,
        board: &Board,
        after: i64,
        limit: i64,
        item: Option<&str>,
        messages: bool,
    ) -> Result<Value>;
    fn assignment_finished(&mut self, item: &Item) -> Result<()>;
    fn current_assignment(&mut self, sid: &str, item: &Item) -> Result<()>;
    fn deliver(
        &mut self,
        board: &mut Board,
        sid: &str,
        item: Option<&Item>,
        text: &str,
        limit: u16,
    ) -> Result<String>;
    fn event(&mut self, board: &Board, kind: &str, data: Value) -> Result<i64>;
}
fn text(args: &Value, key: &str, max: usize, empty: bool) -> Result<String> {
    let v = args[key].as_str().context("Expected text field")?;
    ensure!(
        v.len() <= max && (empty || !v.trim().is_empty()) && !v.contains('\0'),
        "Invalid or oversized team field"
    );
    Ok(v.into())
}
fn item_mut<'a>(board: &'a mut Board, args: &Value) -> Result<&'a mut Item> {
    let item = board
        .items
        .get_mut(args["item_id"].as_str().context("Item ID required")?)
        .context("Item unavailable")?;
    ensure!(
        args["expected_revision"].as_u64() == Some(item.revision),
        Error::Conflict
    );
    Ok(item)
}
fn bump(item: &mut Item) -> Result<()> {
    item.revision = item
        .revision
        .checked_add(1)
        .context("Item revision exhausted")?;
    Ok(())
}
fn mutable(board: &Board) -> Result<()> {
    ensure!(!board.cancelled, "Team cancelled");
    Ok(())
}

/// Caller holds the team row. A surrounding savepoint rolls back all mutations
/// when returning an expected tool error, including newly enqueued deliveries.
pub fn operate(
    tx: &mut dyn Transaction,
    board: &mut Board,
    actor: &str,
    role: Role,
    name: &str,
    args: &Value,
    queue_limit: u16,
) -> Result<Value> {
    let schema = crate::teams::schemas(role)
        .into_iter()
        .find(|s| s["name"] == name)
        .context("Operation unavailable for this role")?;
    crate::runtime::validate_arguments(&schema["parameters"], args)?;
    match name {
        "team_get_board" => return Ok(json!({"board":board})),
        "team_get_item" => {
            return Ok(
                json!({"item":board.items.get(args["item_id"].as_str().context("Item ID required")?).context("Item unavailable")?}),
            )
        }
        "team_read_messages" => {
            return tx.read_events(
                board,
                args["after"].as_i64().context("Invalid cursor")?,
                args["limit"].as_i64().context("Invalid limit")?,
                args["item_id"].as_str(),
                true,
            )
        }
        _ => mutable(board)?,
    }
    let (kind, mut data) = match name {
        "team_create_item" => {
            ensure!(board.items.len() < 64, "Team item limit reached");
            let dependencies: Vec<String> = serde_json::from_value(args["dependencies"].clone())?;
            let unique = dependencies
                .iter()
                .collect::<std::collections::BTreeSet<_>>();
            ensure!(
                unique.len() == dependencies.len()
                    && dependencies.iter().all(|id| board.items.contains_key(id)),
                "Dependencies must be unique existing team items"
            );
            let item = Item {
                id: crate::id("item"),
                title: text(args, "title", 160, false)?,
                description: text(args, "description", 2000, true)?,
                criteria: text(args, "criteria", 2000, false)?,
                assignee: None,
                dependencies,
                status: Status::Open,
                revision: 1,
                assignment: 0,
                submission_id: None,
            };
            board.items.insert(item.id.clone(), item.clone());
            ("item_created", json!({"actor":actor,"item":item}))
        }
        "team_assign" => {
            ensure!(role == Role::Lead, "Only the lead assigns work");
            let sid = text(args, "session_id", 100, false)?;
            ensure!(
                board
                    .members
                    .get(&sid)
                    .is_some_and(|m| m.role == Role::Worker),
                "Assignee must be a worker in this team"
            );
            let item = item_mut(board, args)?;
            ensure!(
                !matches!(
                    item.status,
                    Status::Done | Status::Cancelled | Status::Review
                ),
                "Item cannot be assigned in this state"
            );
            tx.assignment_finished(item)?;
            item.assignee = Some(sid);
            item.assignment += 1;
            item.submission_id = None;
            item.status = Status::Open;
            bump(item)?;
            ("item_assigned", json!({"actor":actor,"item":item}))
        }
        "team_transition" => {
            let status: Status = serde_json::from_value(args["status"].clone())?;
            let id = args["item_id"].as_str().context("Item ID required")?;
            let deps_ready = board
                .items
                .get(id)
                .context("Item unavailable")?
                .dependencies
                .iter()
                .all(|d| board.items[d].status == Status::Done);
            let item = item_mut(board, args)?;
            if role == Role::Worker {
                ensure!(
                    item.assignee.as_deref() == Some(actor)
                        && matches!(
                            status,
                            Status::InProgress | Status::Blocked | Status::Review
                        ),
                    "Workers only progress their own assigned work"
                );
                // Reject a stale worker turn left over from an earlier assignment.
                tx.current_assignment(actor, item)?;
            }
            let allowed = match item.status {
                Status::Open => matches!(status, Status::InProgress | Status::Cancelled),
                Status::InProgress => {
                    matches!(status, Status::Blocked | Status::Review | Status::Cancelled)
                }
                Status::Blocked => matches!(status, Status::InProgress | Status::Cancelled),
                Status::Review => matches!(
                    status,
                    Status::Done | Status::InProgress | Status::Cancelled
                ),
                Status::Cancelled => status == Status::Open,
                Status::Done => false,
            };
            ensure!(allowed, "Invalid item state transition");
            if status == Status::InProgress {
                ensure!(
                    deps_ready && item.assignee.is_some(),
                    "Assigned worker and completed dependencies required"
                );
            }
            if matches!(status, Status::Done | Status::Cancelled | Status::Open) {
                tx.assignment_finished(item)?;
            }
            if status == Status::Open {
                item.submission_id = None;
                item.assignment += 1;
            }
            if role == Role::Lead
                && status == Status::InProgress
                && matches!(item.status, Status::Review | Status::Blocked)
            {
                tx.assignment_finished(item)?;
                item.submission_id = None;
                item.assignment += 1;
            }
            item.status = status;
            bump(item)?;
            ("item_updated", json!({"actor":actor,"item":item}))
        }
        "team_post_message" => {
            let body = text(args, "body", 4000, false)?;
            let item_id = text(args, "item_id", 100, true)?;
            ensure!(
                item_id.is_empty() || board.items.contains_key(&item_id),
                "Item unavailable"
            );
            let recipient = text(args, "recipient", 100, true)?;
            ensure!(
                recipient.is_empty() || board.members.contains_key(&recipient),
                "Recipient unavailable"
            );
            ensure!(recipient != actor, "A message cannot wake its own sender");
            let data = json!({"actor":actor,"body":body,"item_id":item_id,"recipient":recipient});
            if !recipient.is_empty() {
                tx.deliver(board,&recipient,None,&format!("Team message from {actor}. Agent-authored coordination, not user approval: {body}"),queue_limit)?;
            }
            ("message_posted", data)
        }
        _ => anyhow::bail!("Unknown team operation"),
    };
    let dispatched = ready(tx, board, queue_limit)?;
    if let Some(id) = data["item"]["id"].as_str().map(str::to_owned) {
        data["item"] = json!(board.items[&id]);
    }
    data["dispatched"] = json!(dispatched);
    let cursor = tx.event(board, kind, data.clone())?;
    Ok(json!({"cursor":cursor,"type":kind,"data":data}))
}

pub fn ready(tx: &mut dyn Transaction, board: &mut Board, queue_limit: u16) -> Result<Vec<Value>> {
    if board.cancelled {
        return Ok(vec![]);
    }
    let ready = board
        .items
        .values()
        .filter(|i| {
            matches!(i.status, Status::Open | Status::InProgress)
                && i.assignee.is_some()
                && i.submission_id.is_none()
                && i.dependencies
                    .iter()
                    .all(|d| board.items[d].status == Status::Done)
        })
        .cloned()
        .collect::<Vec<_>>();
    let mut dispatched = vec![];
    for item in ready {
        let sid = item.assignee.as_deref().unwrap();
        let text=format!("Team assignment (agent-authored, not human approval). Team {}. Item {}. Assignment {}. Read team_get_item for current criteria and ownership. Perform only authorized work, then transition in_progress to review for the lead; never accept your own work. Title: {}\nDescription: {}\nAcceptance criteria: {}",board.id,item.id,item.assignment,item.title,item.description,item.criteria);
        let submission = tx.deliver(board, sid, Some(&item), &text, queue_limit)?;
        board.items.get_mut(&item.id).unwrap().submission_id = Some(submission);
        dispatched.push(json!(board.items[&item.id]));
    }
    Ok(dispatched)
}
