# Autonomous Research Agent

This repo follows the WAT framework described in `CLAUDE.md`.

## Layout

- `workflows/` - markdown SOPs that define tasks and expected outputs
- `tools/` - deterministic scripts used by workflows
- `.tmp/` - disposable scratch files and intermediate exports

## Notes

- Keep secrets in `.env`
- Do not store deliverables in the repo if they belong in cloud services
- Prefer small, explicit workflows and reusable tools

## Research Workflow

Run the autonomous research workflow with:

```bash
python tools/research_agent.py --query "your topic" --depth standard --output pdf
```

Use `--self-test` to verify the local pipeline without hitting the web.

## Local Dashboard

Run the Streamlit control panel with:

```bash
run_dashboard.bat
```

If you prefer the direct command, use `python -m streamlit run streamlit_app.py`.

The dashboard lets you adjust model settings, filters, run parameters, and download the generated PDF.
