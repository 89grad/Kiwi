# 89grad KiwiTCMS

## Rationale

As the official Dockerhub repo (https://hub.docker.com/r/kiwitcms/kiwi) does
not offer version-tagged releases, this repository serves as a
89grad-controlled 1:1 clone of the upstream repository, except builds are
tagged with the repsective git tags.

## Building

- Check out the git tag you want to build
- Follow the steps taken by the `docker-image` `make` target up to the last step
- As the last step, execute instead: `docker build -t 89grad/kiwi:GIT_TAG_GOES_HERE .`
- Push the built image
