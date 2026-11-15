# sample test pack

A stand-in for `unbork-playbooks` in the backend tests. The manifests copy
the shape of the real actions the tests use (keys, parameters, dispatch
flags, timeouts); the playbooks do nothing. Register it with the
`sample_pack` fixture in `tests/conftest.py`.
