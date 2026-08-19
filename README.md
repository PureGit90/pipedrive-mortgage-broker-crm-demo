# Mortgage Broker Pipeline & Workflow Automation -- Working Automation Demo

## What This Does
Models a Pipedrive-style CRM pipeline for a finance/mortgage brokerage: leads
land tagged by source (Meta, website, referral, phone), move through
deal stages from first contact to settlement, and every stage auto-creates a
task assigned to either the Broker or the VA. It also simulates the specific
approval-gate handoff described in the job post: VA completes data entry,
flags missing information, broker reviews and approves the request, the
request goes to the client, the client responds, and the VA is notified to
resume processing.

## How It Works
Lead capture (Meta/website/referral/phone, tagged on entry) -> deal moves
through 10 pipeline stages -> each stage auto-generates a task with an owner
(Broker or VA) and an SLA -> global task view flags Overdue / Upcoming /
Outstanding across every open deal -> the VA-to-Broker-to-Client approval
gate runs as an interactive handoff, logged to an auditable handoff trail.

## Quick Start
1. `pip install -r requirements.txt`
2. `streamlit run app.py`
3. The pipeline board loads with 8 sample deals already spread across
   stages. Open the "Approval-Gate Handoff" section and step through:
   flag missing info as the VA, review and approve it as the Broker,
   simulate the client responding, then have the VA resume processing.
   Watch the deal move stages and the Global Task View update live.

## Configuration
- No credentials required to run the demo end to end.
- `Anthropic API key` (optional, entered in the sidebar): when set, the
  client-facing "please provide missing info" message is drafted live by
  Claude instead of the built-in template. The approval-gate flow works
  identically either way.

## Demo Limitations
- This is an MVP demo running on 8 sample deals in session state, not a live
  Pipedrive account -- no real Pipedrive, Meta Lead Ads, or SMS/email account
  is connected here.
- Production version would add: the same pipeline and automations built
  natively inside Pipedrive, Meta/Facebook Lead Ads and website form
  integrations feeding leads in automatically, Pipedrive's own
  activity/automation engine creating these tasks instead of app logic,
  email/SMS follow-up sequences triggered per stage, and Zapier/Make
  connectors for anything Pipedrive can't do natively.
