# Legacy files

This directory holds non-functional artifacts from an earlier, abandoned
attempt at this project (~a year before the current pipeline was built).

Most of the `.py` scripts here and `create_views.sql` are **unimplemented
placeholder stubs** — they contain nothing but a `# placeholder` comment and
were never actually written. The `.ps1` runners reference those stubs (and
other scripts that were themselves never implemented), so none of them ever
ran successfully. `README_FAAB_AllInOne_v0_1_9.txt` and
`README_fix_tools_v3.txt` are the original project's own documentation from
that attempt.

None of this is used by, or a dependency of, the current pipeline. It's kept
purely for historical context — to show what the original scaffolding looked
like before the project was rebuilt from `_init_schema.sql` up.
