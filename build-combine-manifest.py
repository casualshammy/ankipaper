import os
from build_common import git, docker, utils

dockerRepo = os.getenv('DOCKER_REPO')
if dockerRepo == None:
  raise ValueError("Env var 'DOCKER_REPO' is not set")

dockerLogin = os.getenv('DOCKER_LOGIN')
if dockerLogin == None:
  raise ValueError("Env var 'DOCKER_LOGIN' is not set")

dockerPassword = os.getenv('DOCKER_PASSWORD')
if dockerPassword == None:
  raise ValueError("Env var 'DOCKER_PASSWORD' is not set")

version = f"{git.get_version_from_current_branch()}.{git.get_last_commit_index()}"

print(f"===========================================", flush=True)
print(f"Combining multi-arch manifest...", flush=True)
print(f"Version: '{version}'", flush=True)
print(f"===========================================", flush=True)

callThrowIfError = utils.callThrowIfError
callThrowIfError(f"docker login -u {dockerLogin} -p {dockerPassword}", True)
try:
  callThrowIfError(
    f"docker buildx imagetools create "
    f"-t {dockerRepo}:{version} "
    f"-t {dockerRepo}:latest "
    f"{dockerRepo}:{version}-amd64 "
    f"{dockerRepo}:{version}-arm64",
    True,
  )
finally:
  callThrowIfError("docker logout", True)

print(f"===========================================", flush=True)
print(f"Done!", flush=True)
print(f"===========================================", flush=True)

git.create_tag_and_push(version, "origin", "casualshammy", True)
utils.callThrowIfError("git stash", True)
git.merge("main", git.get_current_branch_name(), True, "casualshammy", True)
