# Third-party notices

## Wyoming-OpenAI-Gateway

This community app packages the source of
[Wyoming-OpenAI-Gateway](https://github.com/Archmonger/Wyoming-OpenAI-Gateway)
by Archmonger and contributors, pinned to commit
[`a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae`](https://github.com/Archmonger/Wyoming-OpenAI-Gateway/commit/a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae)
(upstream version 0.2.1). Upstream is distributed under the MIT License; its
full copyright and permission notice is included in
`wyoming_openai_gateway/LICENSE.upstream`.

The upstream source is fetched at that immutable commit by the Docker build.
The resolved Python runtime dependencies, including `aiohttp` for the
discovery wrapper, are pinned with hashes in
`wyoming_openai_gateway/requirements.lock`.
Hash-locked source-build prerequisites are listed separately in
`wyoming_openai_gateway/build-requirements.lock`.

The source-build context contains both exact MIT notices and a copy of this
notice. The Dockerfile installs them in the image under
`/usr/share/licenses/wyoming-openai-gateway/`.

Home Assistant and Home Assistant OS are trademarks of their respective
owners. This independent community project is not affiliated with or endorsed
by Home Assistant, OpenAI, or the upstream gateway maintainers.
