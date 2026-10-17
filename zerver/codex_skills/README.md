# Codex skills

The skills Armada gives the agent: each directory is one skill, with its
`SKILL.md`. `zerver/lib/armada_skills.py` provisions them into Codex's home,
with those of the deployment's `ARMADA_CODEX_SKILLS_DIR`, whose skill of the
same name wins.

They are written for the agent, not for this repository's linters, which
skip them (`tools/linter_lib/exclude.py`). A skill's generated files say so
in their skill (`armada-plugin/reference/`, from
`tools/armada/artifact_contract.py`).
