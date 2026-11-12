# Pillow runtime qualification

The Distribution pins Pillow 13.0.0 on every supported Python branch. Odoo's
`release.py` declares Python 3.12 through 3.14; the shipped image uses Python
3.12. Pillow 13 requires Python 3.11 or later, so this update requires no Python
migration or older-version fallback. The existing future-Python marker also
continues to select 13.0.0.

[GHSA-xvh5-g6gv-559v](https://github.com/python-pillow/Pillow/security/advisories/GHSA-xvh5-g6gv-559v)
affects TIFF re-encoding in earlier releases. The HTML editor's
`ir.qweb.field.image.load_remote_url` decodes an image and saves it in its
original format, including compressed TIFF. Ordinary image processing also
registers TIFF support while reading EXIF data; the initial list of registered
formats is not a reliable reachability boundary. Keep the native format and
permission behavior; the library update fixes the encoder.

Pillow is a direct dependency in `requirements.txt`; it is not duplicated in
`docker/constraints.txt`. The latter continues to pin transitive dependencies.
The [13.0.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html)
include API removals and encoding changes, so a successful install alone does
not establish application compatibility.

## Focused proof

Build the test image using the normal Dockerfile. The opt-in codec test runs
inside that image, with no network, private mounts or core dumps:

```bash
docker build --target test -t usl-pillow-qa .
docker run --rm --network none --read-only --memory 128m --pids-limit 64 \
  --ulimit core=0 -e USL_PILLOW_QA=1 \
  -v "$PWD/scripts/tests/test_pillow_runtime.py:/qa_test.py:ro" \
  --entrypoint python usl-pillow-qa /qa_test.py
```

The regression uses a valid one-pixel LZW TIFF with standard ActiveArea
metadata. Its pixel and metadata roundtrip must succeed. It derives from the
valid-metadata portion of the
[upstream libtiff tests](https://github.com/python-pillow/Pillow/blob/13.0.0/Tests/test_file_libtiff.py);
it contains no malformed file or memory-disclosure payload and emits no encoded
image bytes. During qualification on 12 November 2026, Pillow 10.2.0 exited
with SIGSEGV, 13.0.0 passed, and rebuilding with the original requirements
reproduced the SIGSEGV.

For the registry probes, initialize an owned synthetic database with
`test_tools`, `html_editor` and `usl_sign`. Run the native `TestImage`, `TestPdf`
and Sign appearance tests. Then execute the same test file from `odoo shell`
with the native environment injected:

```python
import runpy
runpy.run_path('/qa_test.py', run_name='__main__', init_globals={'ODOO_ENV': env})
env.cr.rollback()
```

Set `USL_PILLOW_QA=1` and mount only the test file at `/qa_test.py`. These probes
exercise the actual HTML-editor TIFF re-save with a fake HTTP response, EXIF
orientation and native image-to-PDF conversion. External providers stay
disabled and no production data is used. Run `python -m pip check` in the built
image and verify marker selection for each supported Python version.

## Qualification boundary

The original Pillow source `42e51b5f4a004981b08225c476c0d4c6f4c9aceb`
was qualified on the earlier `49bb04492412` foundation. Its ARM64 registry
selection reported zero failures/errors in 24 native image/PDF/Sign tests;
all three runtime probes passed. A complete Linux AMD64 Distribution build
also passed the valid TIFF, native EXIF/image-to-PDF and Sign ReportLab image
renderer probes plus `pip check`. Both used Python 3.12.16, Pillow 13.0.0 and
bundled libtiff 4.7.2. That proof belongs to the earlier source.

The integration branch preserves the literal history of PR 425 head
`25fa93bf41b90060a8fc11ac6d61d26c45478229` together with that original Pillow
commit. Requirements, constraints, the normal Dockerfile and the three Pillow
runtime probes retain the scoped dependency change. The exact combined source
requires its own complete AMD64 Distribution build, component/OCA/action-policy
input identities and native offline probes; the earlier image cannot stand in
for its application bytes. Build and probe receipts are recorded on PR 426.

Full registry journeys on the combined image, the fresh-foundation private
upgrade/preservation checks and staging admission remain pending a coordinated
resource slot. Do not pull the new image onto a constrained host before its
placement fits the existing storage reserves. A private fixture owned by
another qualification remains untouched. Production remains affected until
the fixed runtime is deployed.

The separate python-jose advisory GHSA-3qf3-8w2g-rqmx remains open. This Pillow
change neither updates that library nor claims to resolve it. Roll back the
image/dependency pin only through the normal release procedure; doing so
restores the earlier encoder defect. Existing documents, attachment history
and provider configuration are not rewritten by this dependency change.
