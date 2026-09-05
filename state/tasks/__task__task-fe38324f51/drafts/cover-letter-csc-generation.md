# Cover Letter — CSC Generation
## Role: Senior Machine Learning Engineer, Causal & Decision Systems (Remote US / Toronto)

Jonathan Beale
(954) 494-0671 · jonaston015@gmail.com · linkedin.com/in/jonathan-beale

August 16, 2026

CSC Generation Hiring Team

Dear CSC Generation Hiring Team,

Your posting says what you care about is "selecting the right method, not using a particular framework," and that success means "measurable economic lift in controlled experiments," not a better offline metric. That framing is why I'm applying. I build decision-facing ML systems — recommendation and personalization at scale — and I've always trained myself to ask the question your role puts first: not "what predicts the next value" but "what happens *because* we change the input."

Here is the closest I have been to that loop, and I'll be straight about where I sit.

**Decision systems in a regulated commercial domain.** At Mutual of Omaha I owned an ETL pipeline and SLM-based agent system that deduplicated medical and insurance claim records, plus a document transformation pipeline for medical review. Deduplication is a constraint-satisfaction problem dressed as an ML problem: which record is the canonical one depends on downstream policy constraints. That's closer to your "optimize economic outcomes while respecting inventory, margin, vendor, customer, and operational constraints" line than to a pure classification task.

**Recommendation systems at production scale.** At Hewani I architected and deployed an end-to-end recommendation system on Vertex AI (Feature Store, Matching Engine) serving personalized content and product suggestions across multiple app surfaces via RESTful inference APIs — including the monitoring and retraining loop. Recommenders are, at their core, contextual decision-making: choose an action (what to show), observe the outcome (click, conversion), update the policy. The counterfactual/off-policy-evaluation questions in your role (challenger evaluation, exploitation vs. exploration) are the natural next layer on top of exactly the system I run today. I have not yet run champion/challenger policy evaluation at scale — that's a genuine gap I'd name rather than paper over — but the architecture I maintain is the surface on which those techniques land.

**Calibrated evaluation habit.** My hybrid QML biomedical link-prediction project (github.com/iconbaypark2900/hybrid-qml-kg-poc, live demo at hetqml-web.fly.dev) compared quantum support vector classifiers and VQC models head-to-head against Logistic Regression, SVM, and Random Forest baselines on Hetionet, reporting PR-AUC ~0.73. The point of that project in an application to your team is not the quantum part — it's the discipline: I benchmark against the strongest classical baselines I can build, report the honest numbers, optimize embeddings (RotatE + LDA), and keep the repo open. "We care more about exceptional technical ability and judgment than matching a checklist" invites exactly that kind of evidence over a keyword match.

**Python, SQL, large behavioral datasets.** Python has been my primary production language across all three roles; PostgreSQL and SQL appear in the Mutual of Omaha and Hewani data work. Large behavioral datasets — I would call that the claim-records corpus and the recommendation logs I've worked with — is the scale I come from, which is smaller than a big marketplace, and I'd want to be measured on judgment rather than claimed tenure.

One honest gap I want to name up front: my title-level profile is two years of production ML rather than the senior tenure many applicants to this seat will have. What I bring in trade is full-stack ownership (infrastructure, APIs, deployment, monitoring, retraining), fluency in the recommendation/decision-systems literature in practice, and a bias toward shipping small, verifiable, benchmarked systems. Given your "right method, not particular framework" stance, I'd rather earn the senior question with work than with a checklist.

I'd welcome the chance to talk concretely about how a champion/challenger evaluation would attach to a live recommendation loop — I have questions about that, not just answers.

Thank you for your consideration.

Jonathan Beale
