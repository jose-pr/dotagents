# Vendored: coqui_aos

The Agent Overlay Specification's pure-Python reader, copied unchanged from
<https://github.com/Boriken-Dev/coqui-aos> (`python/src/coqui_aos/`), at the commit and with the
file digests that `VENDORED.json` records. Only the modules and the licence are copied. dotagents
imports it as `dotagents._vendor.coqui_aos`; it is not a dependency to install.

Licence: MPL-2.0 (`LICENSE` here), a file-level licence: these files stay under it, unmodified, and
their source is the repository above. The rest of dotagents is MIT.

It needs `ruamel.yaml` at run time (YAML parser events, so a tag, anchor or alias is refused before
it is read) and, below Python 3.11, `tomli`; both are dotagents dependencies.

Do not edit these files. To move to another commit of coqui-aos:

    python tools/vendor_coqui_aos.py --source ../coqui-aos --commit <sha>

It refuses when the checkout is not at that commit or is dirty, and when a file here differs from
its recorded digest; `--check` only verifies the digests (`tests/test_vendored_coqui_aos.py` does).
Then run the suite, which runs the specification's vectors against this copy.
