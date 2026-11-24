# sample test pack

A stand-in for `unbork-playbooks` in the backend tests. The manifests copy
the shape of the real actions the tests use (keys, parameters, dispatch
flags, timeouts); the playbooks do nothing. Register it with the
`sample_pack` fixture in `tests/conftest.py`.

`stateful-upgrade` (per host) and `stateful-cluster-upgrade` (group dispatch)
declare quiesce and unquiesce playbooks. Their quiesce touches
`/tmp/unbork-quiesced` and their unquiesce removes it, so a real run against
a test VM shows the window.
