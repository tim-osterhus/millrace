shadow mode parity fixture

- Extends `control_mailbox` and adds staging plus cutover parity inputs for Phase 08.
- The cutover contract in `bash_reference_autonomy_complete.json` is provenance-rich on purpose:
  it records the source smoke script, source fixture, represented commands, expected exit codes,
  and why QA/CI does not run the full bash cutover path live.
- The normalized comparison reports the cutover bash-side exits as scenario-expected values; live probe
  observations remain nested under provenance because the checked-in research loop does not currently
  complete that path in QA/CI.
