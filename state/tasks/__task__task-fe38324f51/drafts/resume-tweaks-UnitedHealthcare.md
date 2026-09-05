# Resume Tweaks — Senior AI/ML Engineer, UnitedHealthcare

> Tailored to: https://careers.unitedhealthgroup.com/job/minnetonka/sr-ai-ml-engineer-remote/34088/99677568896
> Drafted 2026-08-28. Evidence drawn from resume.txt.

## Which bullets to lead with
1. **Lead with Mutual of Omaha's RAG-over-insurance-documents bullet.** This posting's core is "RAG, NLP, healthcare domain, insurance underwriting, vector DB, MLOps" — the Mutual of Omaha RAG + SLM-agent ETL is the single strongest match on the resume. Put it near the top, in front of the Hewani bullet if you want to emphasize the RAG/healthcare angle.
2. **Second: Hewani's "full deployment lifecycle" bullet** (infra provisioning → containerization → monitoring → retraining). This is your "Vector DB + MLOps + Gen AI/LLM" proof — it shows you own production, not just experiments.
3. **Third: the QML project's evaluation** (QSVC/VQC vs. classical baselines, PR-AUC ~0.73, iterate on the underperformer). This is your "evaluation strategy / measure, debug, improve" evidence.

## What to rephrase in the posting's language
- **"insurance-document RAG" → "RAG over claims/insurance-document corpora"**. The posting says RAG + NLP + healthcare; mirror "RAG," "NLP," "vector databases," "MLOps," "Gen AI."
- **Hewani → "production ML + MLOps on GCP"**. Use "vector databases, MLOps, monitoring, retraining" language.
- **Add scikit-learn by name.** The QML project compares to classical baselines (Logistic Regression, SVM, Random Forest) — that *is* scikit-learn work. Add it to the Technical Skills line so it reads as evidenced, not invented.
- **Add a cloud-agnostic framing.** Since the posting lists Azure: add one honest line that your RAG/MLOps expertise is "cloud-agnostic (built on GCP Vertex AI, transferable to Azure ML)" so the cloud gap reads as a learning line, not a blocker.
- **Healthcare/HIPAA flavor:** your RAG was over *medical and insurance claim records* — name the compliance/privacy dimension ("handling sensitive medical and insurance data") even if you can't formally claim HIPAA certification. It signals domain fit.

## What to cut
- **Snap Inc. and NRG internships** — they are older, non-technical/non-ML, and add no weight for this posting. If space is tight, cut or shrink them; the AI/ML Engineer + ML Solutions Architect roles are all you need here.
- **Qcrypt RNG project** — keep it only if you want to show breadth; it's less relevant than the RAG and QML bullets for *this* JD.

## The honest gaps (and how to handle them)
- **Cloud: GCP vs. Azure.** Pre-empt with the "cloud-agnostic, built on GCP, transferable to Azure" framing above.
- **6+ yrs SE / 2+ yrs production (Senior variant).** The fresher "Sr" posting's core bar is **2+ yrs Gen AI/LLM + equivalent experience** — comfortably met. Lead with the equivalent-experience + ADI-Fellowship story to disarm the degree screen.
- **No degree listed.** This posting explicitly accepts "Bachelor's or equivalent experience (incl. 4+ yrs SE in lieu of a degree)." Lead with experience + ADI Fellowship.

## One-line pitch to keep
"RAG-over-claims and production ML engineer — built semantic search over insurance-document corpora and end-to-end Vertex AI systems — now targeting the same RAG + NLP + MLOps stack in a healthcare-underwriting context."
