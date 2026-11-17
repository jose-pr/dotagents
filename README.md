# dotagents example overlays have moved

The example overlays that lived on this branch are now their own repository,
**[Boriken-Dev/agent-overlays](https://github.com/Boriken-Dev/agent-overlays)**:
`overlays/<name>/` for each overlay, plus a `catalog.toml` at its root.

```sh
export AGENTS_OVERLAYS_REPO="https://github.com/Boriken-Dev/agent-overlays.git@main#overlays"
dotagents overlays add engineering
```

This branch keeps the history of the overlays up to the move, and nothing else.
Its tree no longer carries them: nothing here is maintained.
