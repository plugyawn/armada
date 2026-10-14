The grouped help is rendering now, and the last two open points are closed. `Command.format_options` asks every parameter for its group, keeps the declaration order, and writes one definition list per group. Options that name no group stay under the existing `Options:` heading, so the output of every command that does not use groups is byte for byte what it was.

The column width was the first open point. `HelpFormatter.write_dl` measured the first column on each call, which gave every section its own width and made the descriptions step left and right down the page. The formatter now takes an optional `col_max` and `format_options` computes it once over all groups before it writes any of them.

```python
def format_options(self, ctx, formatter):
    groups = self._collect_option_groups(ctx)
    col_max = max(
        (term_len(term) for rows in groups.values() for term, _ in rows),
        default=0,
    )
    for title, rows in groups.items():
        with formatter.section(title or _("Options")):
            formatter.write_dl(rows, col_max=min(col_max, 30))
```

The second open point was the order of groups when a subclass adds parameters after the parent. I kept it simple and predictable:

- a group appears where its first option is declared
- options that join an existing group later are appended to that group
- the ungrouped section is written first, as it is today
- a group with no visible options is not written at all

What I checked while doing this, in the order I ran it:

1. `pytest tests/test_formatting.py tests/test_options.py -q` passes, 214 tests, with eleven new cases for groups.
2. The full suite passes on Python 3.10 and 3.13; nothing else in the tree calls `write_dl` with positional arguments beyond the rows.
3. `flask --help` and `black --help` print the same text with this branch installed as with the release, which is the compatibility case that matters most.
4. A command with forty options in six groups renders in under a millisecond; the extra pass over the rows is not measurable.

| Case                     | Before                | After                                |
| ------------------------ | --------------------- | ------------------------------------ |
| no groups                | one `Options` section | unchanged                            |
| two groups               | not possible          | two sections, one column width       |
| hidden option in a group | not possible          | omitted, group kept if others remain |
| empty group              | not possible          | not written                          |

One thing I did not do: the Zsh and Fish completion scripts still list options flat. Grouping them there needs a decision about how group titles map to completion tags, and that is a separate change with its own review. I left a note in `docs/shell-completion.rst` and did not touch the completion classes.

The work is on the topic's branch as three commits: the formatter argument, the grouping itself, and the documentation with a changelog entry. Nothing is pushed. If the order rule above is not the one you want, the place to change it is `_collect_option_groups`, which is twenty lines and has its own tests.
