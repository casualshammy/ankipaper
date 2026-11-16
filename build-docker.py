import os
from build_common import git, docker, packages

dockerRepo = os.getenv('DOCKER_REPO')
if dockerRepo == None:
  raise ValueError("Env var 'DOCKER_REPO' is not set")

dockerLogin = os.getenv('DOCKER_LOGIN')
if dockerLogin == None:
  raise ValueError("Env var 'DOCKER_LOGIN' is not set")

dockerPassword = os.getenv('DOCKER_PASSWORD')
if dockerPassword == None:
  raise ValueError("Env var 'DOCKER_PASSWORD' is not set")

buildArch = os.getenv('BUILD_ARCH')
if buildArch == None:
  raise ValueError("Env var 'BUILD_ARCH' is not set")

version = f"{git.get_version_from_current_branch()}.{git.get_last_commit_index()}"

print(f"===========================================", flush=True)
print(f"Creating docker image...", flush=True)
print(f"Version: '{version}'", flush=True)
print(f"Arch: '{buildArch}'", flush=True)
print(f"===========================================", flush=True)

packages.adjust_python_version(os.path.join("app", "__init__.py"), version)

docker.buildPushMultiArch(
  _tags=[f"{dockerRepo}:{version}-{buildArch}"],
  _dockerfile="Dockerfile",
  _login=dockerLogin,
  _pass=dockerPassword,
  _platforms=[f"linux/{buildArch}"],
)

print(f"===========================================", flush=True)
print(f"Done!", flush=True)
print(f"===========================================", flush=True)
