import json
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Mortgage Broker Pipeline Demo", page_icon="🏦", layout="wide")

# ---------------------------------------------------------------------------
# Pipeline / stage configuration
# Mirrors the client's 15-step VA-to-broker processing flow, simplified into
# a Pipedrive-style deal-stage pipeline with owner + SLA per stage.
# ---------------------------------------------------------------------------

STAGE_ORDER = [
    "New Lead",
    "Qualified",
    "Application Started",
    "VA Processing",
    "Broker Review",
    "Client Info Requested",
    "Client Responded",
    "Submitted",
    "Approved",
    "Declined",
]

STAGE_CONFIG = {
    "New Lead": {
        "owner": "Broker",
        "task": "Make first contact with lead within SLA",
        "sla_days": 1,
    },
    "Qualified": {
        "owner": "Broker",
        "task": "Send application checklist, confirm loan goals on discovery call",
        "sla_days": 2,
    },
    "Application Started": {
        "owner": "VA",
        "task": "Begin data entry into loan-processing software",
        "sla_days": 2,
    },
    "VA Processing": {
        "owner": "VA",
        "task": "Complete data entry, flag any missing information",
        "sla_days": 3,
    },
    "Broker Review": {
        "owner": "Broker",
        "task": "Review VA's missing-info request, approve or edit before it goes to client",
        "sla_days": 2,
    },
    "Client Info Requested": {
        "owner": "VA",
        "task": "Awaiting client response, follow up if overdue",
        "sla_days": 4,
    },
    "Client Responded": {
        "owner": "VA",
        "task": "Resume processing now that client info is in",
        "sla_days": 1,
    },
    "Submitted": {
        "owner": "Broker",
        "task": "Monitor lender assessment, chase valuation and conditions",
        "sla_days": 5,
    },
    "Approved": {
        "owner": "Broker",
        "task": "Prepare loan documents for settlement",
        "sla_days": 3,
    },
    "Declined": {
        "owner": "Broker",
        "task": "Notify client, explore alternative lenders",
        "sla_days": 2,
    },
}

SOURCE_COLORS = {
    "Meta": "🔵",
    "Website": "🟢",
    "Referral": "🟣",
    "Phone": "🟠",
}


# ---------------------------------------------------------------------------
# Data / state
# ---------------------------------------------------------------------------

def load_sample_deals() -> list[dict]:
    with open("sample_data/deals.json") as f:
        return json.load(f)


def init_state():
    if "deals" not in st.session_state:
        st.session_state["deals"] = load_sample_deals()
    if "handoff_log" not in st.session_state:
        st.session_state["handoff_log"] = []
    if "pending_request" not in st.session_state:
        st.session_state["pending_request"] = {}
    if "drafted_message" not in st.session_state:
        st.session_state["drafted_message"] = None


def get_deal(deal_id: str) -> dict:
    for d in st.session_state["deals"]:
        if d["deal_id"] == deal_id:
            return d
    raise KeyError(deal_id)


def log_handoff(deal_id: str, client_name: str, from_stage: str, to_stage: str, note: str):
    st.session_state["handoff_log"].append(
        {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "deal_id": deal_id,
            "client_name": client_name,
            "from_stage": from_stage,
            "to_stage": to_stage,
            "note": note,
        }
    )


def move_deal(deal_id: str, new_stage: str, note: str):
    deal = get_deal(deal_id)
    old_stage = deal["stage"]
    log_handoff(deal_id, deal["client_name"], old_stage, new_stage, note)
    deal["stage"] = new_stage
    deal["days_in_stage"] = 0


# ---------------------------------------------------------------------------
# Task engine -- every deal's current stage auto-generates a task with an
# owner (Broker or VA) and an SLA. This is the "tasks/activities are
# automatically created based on deal stage" requirement from the job post.
# ---------------------------------------------------------------------------

def task_status(days_in_stage: int, sla_days: int) -> str:
    if days_in_stage > sla_days:
        return "Overdue"
    if days_in_stage >= sla_days - 1:
        return "Upcoming"
    return "Outstanding"


def build_task_board(deals: list[dict]) -> pd.DataFrame:
    rows = []
    for d in deals:
        if d["stage"] in ("Approved", "Declined"):
            continue  # terminal stages -- closing task still shown, but not "open pipeline" work
        cfg = STAGE_CONFIG[d["stage"]]
        status = task_status(d["days_in_stage"], cfg["sla_days"])
        rows.append(
            {
                "Deal": d["client_name"],
                "Deal ID": d["deal_id"],
                "Source": d["lead_source"],
                "Stage": d["stage"],
                "Task": cfg["task"],
                "Owner": cfg["owner"],
                "Days in stage": d["days_in_stage"],
                "SLA (days)": cfg["sla_days"],
                "Status": status,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Optional AI-assisted step: drafting the client-facing "please provide
# missing info" message. Falls back to a clean template when no API key is
# set -- the demo runs fully standalone with zero credentials required.
# ---------------------------------------------------------------------------

def _template_client_message(client_name: str, missing_info: str, broker_note: str) -> str:
    note_line = f"\n\n{broker_note}" if broker_note else ""
    return (
        f"Hi {client_name.split()[0]},\n\n"
        f"Thanks for your patience while we progress your application. To keep things moving "
        f"with the lender, could you send through the following:\n\n"
        f"{missing_info}\n\n"
        f"Once we have this we can continue straight away.{note_line}\n\n"
        f"Thanks,\nYour broker"
    )


def generate_client_message(client_name: str, missing_info: str, broker_note: str, api_key: str) -> tuple[str, str]:
    """Returns (message, source_label)."""
    if not api_key:
        return _template_client_message(client_name, missing_info, broker_note), "template (no API key set)"
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "Write a short, warm, professional email from a mortgage broker to a client, "
            "requesting specific missing information needed to continue processing their loan "
            "application. Keep it under 100 words, no subject line, no placeholders.\n\n"
            f"Client first name: {client_name.split()[0]}\n"
            f"Missing information needed: {missing_info}\n"
            f"Broker's note to include (optional context): {broker_note or 'none'}\n"
        )
        message = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip(), "live Claude API"
    except Exception as exc:  # pragma: no cover - network/env dependent
        st.warning(f"Claude API call failed, falling back to template: {exc}")
        return _template_client_message(client_name, missing_info, broker_note), "template (Claude call failed)"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render_pipeline_board(deals: list[dict], source_filter: list[str]):
    st.subheader("1. Deal Pipeline")
    st.caption(
        "Every lead is tagged by source on capture (Meta, Website, Referral, Phone) and moves "
        "left to right through the stages below. Filter by source in the sidebar."
    )
    filtered = [d for d in deals if d["lead_source"] in source_filter]
    cols = st.columns(len(STAGE_ORDER))
    for col, stage in zip(cols, STAGE_ORDER):
        stage_deals = [d for d in filtered if d["stage"] == stage]
        with col:
            st.markdown(f"**{stage}**")
            st.caption(f"{len(stage_deals)} deal(s)")
            for d in stage_deals:
                icon = SOURCE_COLORS.get(d["lead_source"], "⚪")
                st.markdown(
                    f"{icon} **{d['client_name']}**  \n"
                    f"${d['loan_amount']:,.0f}  \n"
                    f"`{d['deal_id']}` · {d['days_in_stage']}d in stage"
                )


def render_task_view(deals: list[dict]):
    st.subheader("2. Global Task View")
    st.caption(
        "Tasks are not entered manually -- they're auto-created from each deal's current stage, "
        "with an owner (Broker or VA) and a status derived from time-in-stage vs. SLA."
    )
    board = build_task_board(deals)
    if board.empty:
        st.info("No open tasks.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Overdue", int((board["Status"] == "Overdue").sum()))
    c2.metric("Upcoming (due soon)", int((board["Status"] == "Upcoming").sum()))
    c3.metric("Outstanding (open)", int((board["Status"] == "Outstanding").sum()))

    owner_filter = st.multiselect("Filter by owner", ["Broker", "VA"], default=["Broker", "VA"])
    status_order = {"Overdue": 0, "Upcoming": 1, "Outstanding": 2}
    view = board[board["Owner"].isin(owner_filter)].copy()
    view["_sort"] = view["Status"].map(status_order)
    view = view.sort_values(["_sort", "Days in stage"], ascending=[True, False]).drop(columns="_sort")

    def flag(status):
        return {"Overdue": "🔴", "Upcoming": "🟡", "Outstanding": "⚪"}.get(status, "")

    view["Status"] = view["Status"].apply(lambda s: f"{flag(s)} {s}")
    st.dataframe(view, use_container_width=True, hide_index=True)


def render_approval_gate(api_key: str):
    st.subheader("3. Approval-Gate Handoff (VA -> Broker -> Client -> VA)")
    st.caption(
        "This is the specific mechanic from the job post: VA completes data entry, flags missing "
        "info, broker reviews and approves the request, request goes to client, client responds, "
        "VA is notified and resumes processing. Each step below actually moves the deal and logs "
        "the handoff."
    )

    deals = st.session_state["deals"]
    va_deals = [d for d in deals if d["stage"] == "VA Processing"]
    review_deals = [d for d in deals if d["stage"] == "Broker Review"]
    requested_deals = [d for d in deals if d["stage"] == "Client Info Requested"]
    responded_deals = [d for d in deals if d["stage"] == "Client Responded"]

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Step A: VA flags missing info", "Step B: Broker reviews", "Step C: Client responds", "Handoff log"]
    )

    with tab1:
        if not va_deals:
            st.info("No deals currently in VA Processing.")
        else:
            options = {f"{d['client_name']} ({d['deal_id']})": d["deal_id"] for d in va_deals}
            choice = st.selectbox("Deal in VA Processing", list(options.keys()), key="va_choice")
            deal_id = options[choice]
            missing_info = st.text_area(
                "Missing information identified",
                value="Last 2 years of business tax returns and 3 months of bank statements",
                key=f"missing_{deal_id}",
            )
            if st.button("Flag missing info -> send to Broker Review", key=f"flag_{deal_id}"):
                move_deal(deal_id, "Broker Review", f"VA flagged missing info: {missing_info}")
                st.session_state["pending_request"][deal_id] = missing_info
                st.success(f"{choice} moved to Broker Review. Task now assigned to Broker.")
                st.rerun()

    with tab2:
        if not review_deals:
            st.info("No deals currently waiting on Broker Review.")
        else:
            options = {f"{d['client_name']} ({d['deal_id']})": d["deal_id"] for d in review_deals}
            choice = st.selectbox("Deal in Broker Review", list(options.keys()), key="review_choice")
            deal_id = options[choice]
            missing_info = st.session_state["pending_request"].get(
                deal_id, "Last 2 years of business tax returns and 3 months of bank statements"
            )
            st.text_area("VA's flagged request", value=missing_info, disabled=True, key=f"ro_{deal_id}")
            broker_note = st.text_input("Broker's note to add before sending (optional)", key=f"note_{deal_id}")
            decision = st.radio("Broker decision", ["Approve and send to client", "Send back to VA"], key=f"dec_{deal_id}")

            if st.button("Confirm decision", key=f"confirm_{deal_id}"):
                deal = get_deal(deal_id)
                if decision == "Send back to VA":
                    move_deal(deal_id, "VA Processing", f"Broker sent back for rework: {broker_note or 'no note'}")
                    st.success(f"{choice} sent back to VA Processing.")
                else:
                    message, source = generate_client_message(
                        deal["client_name"], missing_info, broker_note, api_key
                    )
                    st.session_state["drafted_message"] = {"deal_id": deal_id, "message": message, "source": source}
                    move_deal(deal_id, "Client Info Requested", "Broker approved, request sent to client")
                    st.success(f"{choice} approved. Request sent to client, deal moved to Client Info Requested.")
                st.rerun()

        drafted = st.session_state.get("drafted_message")
        if drafted:
            st.divider()
            st.markdown(f"**Client-facing message sent** (source: {drafted['source']})")
            st.text_area("Message", value=drafted["message"], height=160, disabled=True)

    with tab3:
        if not requested_deals:
            st.info("No deals currently awaiting client response.")
        else:
            options = {f"{d['client_name']} ({d['deal_id']})": d["deal_id"] for d in requested_deals}
            choice = st.selectbox("Deal awaiting client", list(options.keys()), key="client_choice")
            deal_id = options[choice]
            if st.button("Simulate client response received", key=f"resp_{deal_id}"):
                move_deal(deal_id, "Client Responded", "Client provided requested information")
                st.success(f"{choice} moved to Client Responded. VA notified to resume processing.")
                st.rerun()

        if responded_deals:
            st.divider()
            st.caption("Deals ready for VA to resume:")
            for d in responded_deals:
                col1, col2 = st.columns([3, 1])
                col1.write(f"**{d['client_name']}** ({d['deal_id']}) -- client info received")
                if col2.button("VA resumes -> back to VA Processing", key=f"resume_{d['deal_id']}"):
                    move_deal(d["deal_id"], "VA Processing", "VA resumed processing with new client info")
                    st.rerun()

    with tab4:
        log = st.session_state["handoff_log"]
        if not log:
            st.caption("No handoffs yet this session -- run through the steps above.")
        else:
            log_df = pd.DataFrame(log).iloc[::-1]
            st.dataframe(log_df, use_container_width=True, hide_index=True)
            csv = log_df.to_csv(index=False).encode("utf-8")
            st.download_button("Download handoff log (CSV)", csv, "handoff_log.csv", "text/csv")


def render_new_lead_form():
    st.subheader("4. Lead Capture")
    st.caption(
        "Leads land here automatically from Meta/Facebook lead ads, the website contact form, "
        "referrals, or phone/manual entry -- each tagged with source on capture, matching the "
        "job spec's 'leads are automatically captured and assigned the correct source'."
    )
    with st.form("new_lead_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Client name")
        source = c2.selectbox("Lead source", list(SOURCE_COLORS.keys()))
        loan_amount = c3.number_input("Loan amount ($)", min_value=0, value=500000, step=10000)
        submitted = st.form_submit_button("Capture lead")
        if submitted and name:
            new_id = f"D-{1000 + len(st.session_state['deals']) + 1}"
            new_deal = {
                "deal_id": new_id,
                "client_name": name,
                "lead_source": source,
                "loan_amount": loan_amount,
                "stage": "New Lead",
                "days_in_stage": 0,
                "notes": f"Captured via {source} lead form.",
            }
            st.session_state["deals"].append(new_deal)
            log_handoff(new_id, name, "-", "New Lead", f"Lead captured from {source}")
            st.success(f"{name} captured as a new lead ({source}), task assigned to Broker for first contact.")
            st.rerun()


def main():
    init_state()
    st.title("🏦 Mortgage Broker Pipeline & Workflow Automation")
    st.caption(
        "Models your exact lead-to-settlement pipeline in Pipedrive: multi-source lead capture, "
        "stage-based task automation, and the VA-to-broker approval-gate handoff for missing "
        "client information."
    )

    with st.sidebar:
        st.header("Data")
        st.caption(f"{len(st.session_state['deals'])} sample deals loaded, moving through the pipeline live.")
        if st.button("Reset demo data"):
            st.session_state.clear()
            st.rerun()
        st.divider()
        st.header("Filter")
        source_filter = st.multiselect(
            "Lead source", list(SOURCE_COLORS.keys()), default=list(SOURCE_COLORS.keys())
        )
        st.divider()
        st.header("AI-Assisted Drafting (optional)")
        api_key = st.text_input(
            "Anthropic API key (optional)",
            type="password",
            help="Used only to draft the client-facing 'please provide missing info' message. "
            "Leave blank to use the built-in template -- the demo works fully without it.",
        )
        st.divider()
        st.caption(
            "This is a standalone simulation of the Pipedrive setup -- no live Pipedrive, Meta, "
            "or SMS/email account is connected. In your build, lead capture, stage automation, "
            "and this same approval-gate logic run natively inside Pipedrive with real "
            "integrations wired in."
        )

    render_pipeline_board(st.session_state["deals"], source_filter)
    st.divider()
    render_task_view(st.session_state["deals"])
    st.divider()
    render_approval_gate(api_key)
    st.divider()
    render_new_lead_form()

    st.divider()
    st.caption(
        "This is an MVP demo running on sample data with a simulated Pipedrive pipeline (session "
        "state, not a live Pipedrive account). Production version: real Pipedrive pipeline with "
        "these exact stages, Meta Lead Ads + website form integrations feeding the CRM directly, "
        "Pipedrive automations creating these tasks natively, and SMS/email follow-up sequences "
        "triggered at each stage. See workflow.md for the full architecture."
    )


if __name__ == "__main__":
    main()
