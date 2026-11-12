import os
import re
from build_common import git, docker

appInitPath = os.path.join("app", "__init__.py")

def _adjust_app_version(_version: str) -> None:
  # Stamps the build version into app/__init__.py (Python analog of adjust_csproj_version).
  if (not os.path.isfile(appInitPath)):
    raise FileNotFoundError(f"File '{appInitPath}' is not found")
  with open(appInitPath, "r", encoding="utf-8") as appInitFile:
    content = appInitFile.read()
  adjustedContent, matchCount = re.subn(r'^__version__ = ".*"$', f'__version__ = "{_version}"', content, flags=re.MULTILINE)
  if (matchCount != 1):
    raise Exception(f"Can't adjust version in file '{appInitPath}'")
  with open(appInitPath, "w", encoding="utf-8") as appInitFile:
    appInitFile.write(adjustedContent)
  print(f"Adjusted version in file '{appInitPath}'", flush=True)

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
_adjust_app_version(version)
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
