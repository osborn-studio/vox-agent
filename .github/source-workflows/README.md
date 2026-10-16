# Non-executable CI source references

The three `.yml.txt` files preserve the original workflow definitions byte for
byte. They are intentionally outside `.github/workflows` and do not have an
active workflow extension. Some reference self-hosted infrastructure or release
permissions from private development; they must not be activated in this public
repository without a separate security, permissions and cost review.

GitHub Actions is disabled at repository level before the initial source push.
No private secrets, repository variables, runners or release/signing credentials
are copied. The source tests that inspect these definitions read this directory;
this archival move does not remove their original assertions or change the
private repository's workflows/protections.
