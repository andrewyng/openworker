//! Mutable price observations are separate from immutable provisioning intent.
//! Refreshing a quote must never regenerate resource identities or replay writes.
use super::*;
const QUOTE_SECONDS: u64 = 1800;
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Quote {
    review_digest: String,
    observed_at_unix: u64,
    valid_until_unix: u64,
    price: resolve::Price,
    max_estimated_cost_usd: Option<u32>,
    estimate_usd: Option<f64>,
    ceiling_check: String,
    digest: String,
}
impl Quote {
    fn digest(&self) -> Result<String> {
        let mut v = serde_json::to_value(self)?;
        v.as_object_mut().unwrap().remove("digest");
        Ok(hash(&serde_json::to_vec(&v)?))
    }
}
fn check(price: &resolve::Price, ceiling: Option<u32>) -> (Option<f64>, &'static str) {
    let estimate = price
        .total_hourly_usd
        .filter(|n| n.is_finite() && *n > 0.0)
        .filter(|_| price.unpriced_components.is_empty())
        .map(|n| n * f64::from(price.planned_hours) + f64::from(price.variable_allowance_usd))
        .filter(|n| n.is_finite() && *n > 0.0);
    let result = match (ceiling, estimate) {
        (None, _) => "not_requested",
        (Some(_), None) => "incomplete",
        (Some(c), Some(v)) if v > f64::from(c) => "exceeded",
        _ => "within_estimated_ceiling",
    };
    (estimate, result)
}
pub(super) async fn review(
    root: &Path,
    review: &Review,
    package: &package::Snapshot,
    ceiling: Option<u32>,
) -> Result<()> {
    let mut nodes = vec![review.resolution.node.clone()];
    pricing::price_nodes(&review.config, &mut nodes).await;
    let price = pricing::estimate(&review.config, &nodes[0], &package.setup.infrastructure).await;
    let (estimate, status) = check(&price, ceiling);
    let at = now()?;
    let mut quote = Quote {
        review_digest: review.digest.clone(),
        observed_at_unix: at,
        valid_until_unix: at + QUOTE_SECONDS,
        price,
        max_estimated_cost_usd: ceiling,
        estimate_usd: estimate,
        ceiling_check: status.into(),
        digest: String::new(),
    };
    quote.digest = quote.digest()?;
    write_private(&root.join("cost-estimate.json"), &quote)?;
    crate::output(
        json!({"phase":"cost_review","quote":quote,"mutations":0,"hardSpendingCap":false,"scope":"modeled fixed infrastructure and explicit usage allowance only","excludedCosts":["actual LLM usage","variable traffic/storage/requests/logging","discounts, taxes and unmodeled services"],"unknownPricesAreZero":false}),
    )?;
    ensure!(status!="incomplete","Estimated-cost ceiling cannot be checked: modeled prices are missing or ambiguous. No cloud mutation performed");
    ensure!(
        status != "exceeded",
        "Estimated cost exceeds the plan-mode ceiling. No cloud mutation performed"
    );
    Ok(())
}
pub(super) fn validate_approval(root: &Path, review: &Review) -> Result<()> {
    // An already dispatched deployment retains its original approval. Quote
    // freshness is an admission check, never an excuse to recreate resources.
    if root.join("up-journal.json").exists() {
        return Ok(());
    }
    let path = root.join("cost-estimate.json");
    if !path.exists() {
        let at = now()?;
        ensure!(review.resolution.observed_at_unix<=at && at-review.resolution.observed_at_unix<=QUOTE_SECONDS,"Saved price observation is stale. Rerun --plan-only to refresh costs without changing the deployment intent");
        return Ok(());
    }
    let quote: Quote = serde_json::from_slice(&fs::read(path)?)?;
    validate(&quote, review, now()?)
}
fn validate(q: &Quote, r: &Review, at: u64) -> Result<()> {
    ensure!(
        q.digest == q.digest()? && q.review_digest == r.digest,
        "Cost quote does not match the reviewed deployment"
    );
    ensure!(
        q.observed_at_unix <= at
            && at <= q.valid_until_unix
            && q.valid_until_unix - q.observed_at_unix == QUOTE_SECONDS,
        "Cost quote expired; rerun --plan-only. Resource and operation identities are unchanged"
    );
    let (estimate, status) = check(&q.price, q.max_estimated_cost_usd);
    ensure!(
        estimate == q.estimate_usd && status == q.ceiling_check,
        "Cost quote result changed"
    );
    ensure!(
        ["not_requested", "within_estimated_ceiling"].contains(&status),
        "Plan-mode estimated-cost check did not pass; inspect cost-estimate.json"
    );
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    fn price() -> resolve::Price {
        serde_json::from_value(json!({"currency":"USD","workerHourlyUsd":0.2,"totalHourlyUsd":0.5,"knownHourlySubtotalUsd":0.5,"components":{},"variableAllowanceUsd":25,"plannedHours":6,"unpricedComponents":[],"hardCap":false})).unwrap()
    }
    #[test]
    fn ceiling_is_plan_estimate_not_zero_for_unknown_or_a_spending_cap() {
        let mut p = price();
        assert_eq!(
            check(&p, Some(100)),
            (Some(28.0), "within_estimated_ceiling")
        );
        assert_eq!(check(&p, Some(27)).1, "exceeded");
        p.unpriced_components.push("network".into());
        assert_eq!(check(&p, Some(100)), (None, "incomplete"));
        assert_eq!(check(&p, None).1, "not_requested");
        p.unpriced_components.clear();
        p.total_hourly_usd = None;
        assert_eq!(check(&p, Some(100)).1, "incomplete");
    }
}

#[cfg(test)]
mod receipt_tests {
    use super::*;
    #[test]
    fn quote_refresh_preserves_intent_and_expired_failed_or_changed_receipts_cannot_admit() {
        let config:Config=serde_yaml::from_str("name: example\ncloud: {provider: aws, accountId: '123456789012', region: us-west-2}\nnodes: {count: 2, instanceType: t4g.large}\n").unwrap();
        let price:resolve::Price=serde_json::from_value(json!({"currency":"USD","workerHourlyUsd":0.2,"totalHourlyUsd":0.5,"knownHourlySubtotalUsd":0.5,"components":{},"variableAllowanceUsd":25,"plannedHours":6,"unpricedComponents":[],"hardCap":false})).unwrap();
        let r=Review{schema_version:1,config_digest:config.digest().unwrap(),config,deployment_id:"original".into(),resolution:serde_json::from_value(json!({"node":{"instanceType":"t4g.large","cpu":2,"memoryGiB":8,"architecture":"arm64","eligible":true,"hourlyUsd":0.1},"price":price,"observedAtUnix":1000,"identity":{}})).unwrap(),package_digest:"release".into(),native_bundle_digest:"original-bundle".into(),digest:"original-review".into()};
        let mut q = Quote {
            review_digest: r.digest.clone(),
            observed_at_unix: 2000,
            valid_until_unix: 2000 + QUOTE_SECONDS,
            price,
            max_estimated_cost_usd: Some(100),
            estimate_usd: Some(28.0),
            ceiling_check: "within_estimated_ceiling".into(),
            digest: String::new(),
        };
        q.digest = q.digest().unwrap();
        validate(&q, &r, 2001).unwrap();
        assert!(validate(&q, &r, 1999).is_err());
        assert!(validate(&q, &r, 4000).is_err());
        q.review_digest = "other-plan".into();
        q.digest = q.digest().unwrap();
        assert!(validate(&q, &r, 2001).is_err());
        q.review_digest = r.digest.clone();
        q.max_estimated_cost_usd = Some(1);
        q.ceiling_check = "exceeded".into();
        q.digest = q.digest().unwrap();
        assert!(validate(&q, &r, 2001).is_err());
        assert_eq!(r.deployment_id, "original");
        assert_eq!(r.native_bundle_digest, "original-bundle");
    }
}
