# Offline verification inputs

Five unchanged registry archives are pinned by source-patches.json (SHA256 and
SHA512), used only to reproduce the private derivatives. The reference archive
contains ordinary licensed files from micromatch 4.0.8 and its six-package
closure; it receives only the bounded semantic cases in matcher_parity, never
attack inputs. It is not installed into the app and is not a production fallback.
All archive files retain their package licenses. Legacy rsa.js/parse.js copies
are the previously reviewed historical patches, pinned by
mobile-dependency-patches.json; their original MIT licenses are included.
The safety table pins every input byte and CI's reviewed workflow pins that table.
No network, account, certificate or package lifecycle is used to prepare inputs.
