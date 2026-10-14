# Exclude some directories and files from lint checking
EXCLUDED_FILES = [
    # Third-party code that doesn't match our style
    "web/third",
    # INTERIM BETA (/beta/): the design prototype's kit and a spike moved
    # in whole, to be deleted when the rebuild's conversation lands. It is
    # not written to web/src/armada/'s rules and will never be brought up
    # to them; see web/src/armada/beta/README.md.
    "web/src/armada/beta",
]

PUPPET_CHECK_RULES_TO_EXCLUDE = [
    "--no-documentation-check",
    "--no-80chars-check",
]
