#!/usr/bin/env python3

"""Temporary helper to collect selected Isaac ROS package metadata."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


API_ROOT = "https://api.github.com"
DEFAULT_ORG = "NVIDIA-ISAAC-ROS"
DEFAULT_OUTPUT = "rosisaac_additional_recipes.yaml"
DEFAULT_VINCA_OUTPUT = "isaac_vinca.yaml"
TARGET_REPOSITORIES = [
    "isaac_ros_apriltag",
    "isaac_ros_benchmark",
    "isaac_ros_common",
    "isaac_ros_cloud_control",
    "isaac_ros_compression",
    "isaac_ros_cumotion",
    "isaac_ros_dnn_inference",
    "isaac_ros_dnn_stereo_depth",
    "isaac_ros_image_pipeline",
    "isaac_ros_image_segmentation",
    "isaac_ros_jetson",
    "isaac_ros_mapping_and_localization",
    "isaac_ros_nitros",
    "isaac_ros_nvblox",
    "isaac_ros_object_detection",
    "isaac_ros_pose_estimation",
    "isaac_ros_sipl_camera",
    "isaac_ros_visual_slam",
]


@dataclass(frozen=True)
class RepoRef:
    name: str
    clone_url: str
    default_branch: str
    ref: str
    ref_key: str


@dataclass(frozen=True)
class PackageEntry:
    package_name: str
    version: str
    repo_name: str
    clone_url: str
    ref: str
    ref_key: str
    manifest_path: str


class GitHubClient:
    def __init__(self, token: Optional[str] = None, pause_seconds: float = 0.2):
        self._token = token
        self._pause_seconds = pause_seconds

    def get_json(self, url: str):
        request = urllib.request.Request(
            url,
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read().decode("utf-8")
                if self._pause_seconds:
                    time.sleep(self._pause_seconds)
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API request failed for {url}: {exc.code} {body}") from exc

    def iter_paginated(self, url: str) -> Iterable[dict]:
        page = 1
        while True:
            separator = "&" if "?" in url else "?"
            page_url = f"{url}{separator}per_page=100&page={page}"
            items = self.get_json(page_url)
            if not items:
                return
            for item in items:
                yield item
            page += 1

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ros-jazzy-isaac-scraper",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect selected Isaac ROS package metadata from a GitHub "
            "organization and emit a rosdistro_additional_recipes-style YAML file."
        )
    )
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"GitHub organization (default: {DEFAULT_ORG})")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output YAML path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--vinca-output",
        default=DEFAULT_VINCA_OUTPUT,
        help=f"Output YAML path for vinca package list (default: {DEFAULT_VINCA_OUTPUT})",
    )
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing a GitHub token for higher rate limits",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.2,
        help="Delay between API requests to avoid bursts (default: 0.2)",
    )
    return parser.parse_args()


def get_repo(client: GitHubClient, org: str, repo_name: str) -> dict:
    url = f"{API_ROOT}/repos/{urllib.parse.quote(org)}/{urllib.parse.quote(repo_name)}"
    return client.get_json(url)


def select_repo_ref(client: GitHubClient, repo: dict) -> RepoRef:
    org = repo["owner"]["login"]
    name = repo["name"]
    default_branch = repo["default_branch"]
    clone_url = repo["clone_url"]

    latest_release_url = (
        f"{API_ROOT}/repos/{urllib.parse.quote(org)}/{urllib.parse.quote(name)}/releases/latest"
    )
    try:
        latest_release = client.get_json(latest_release_url)
        return RepoRef(
            name=name,
            clone_url=clone_url,
            default_branch=default_branch,
            ref=latest_release["tag_name"],
            ref_key="tag",
        )
    except RuntimeError as exc:
        if " 404 " not in str(exc):
            raise

    tags_url = f"{API_ROOT}/repos/{urllib.parse.quote(org)}/{urllib.parse.quote(name)}/tags?per_page=1"
    tags = client.get_json(tags_url)
    if tags:
        return RepoRef(
            name=name,
            clone_url=clone_url,
            default_branch=default_branch,
            ref=tags[0]["name"],
            ref_key="tag",
        )

    branch_url = (
        f"{API_ROOT}/repos/{urllib.parse.quote(org)}/{urllib.parse.quote(name)}/branches/"
        f"{urllib.parse.quote(default_branch)}"
    )
    branch = client.get_json(branch_url)
    return RepoRef(
        name=name,
        clone_url=clone_url,
        default_branch=default_branch,
        ref=branch["commit"]["sha"],
        ref_key="rev",
    )


def list_tree_paths(client: GitHubClient, repo: dict, ref: str) -> List[str]:
    org = repo["owner"]["login"]
    name = repo["name"]
    tree_url = (
        f"{API_ROOT}/repos/{urllib.parse.quote(org)}/{urllib.parse.quote(name)}/git/trees/"
        f"{urllib.parse.quote(ref)}?recursive=1"
    )
    tree = client.get_json(tree_url)
    return [item["path"] for item in tree.get("tree", []) if item.get("type") == "blob"]


def fetch_text_file(client: GitHubClient, org: str, repo_name: str, ref: str, path: str) -> str:
    encoded_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    raw_url = f"https://raw.githubusercontent.com/{org}/{repo_name}/{urllib.parse.quote(ref)}/{encoded_path}"
    request = urllib.request.Request(raw_url, headers={"User-Agent": "ros-jazzy-isaac-scraper"})
    with urllib.request.urlopen(request) as response:
        return response.read().decode("utf-8")


def discover_manifest_paths(paths: Iterable[str]) -> List[str]:
    manifests = []
    for path in paths:
        filename = path.rsplit("/", 1)[-1]
        if filename.startswith("package") and filename.endswith(".xml"):
            manifests.append(path)

    manifests = sorted(manifests)
    filtered_manifests: List[str] = []
    manifest_dirs = set()

    for manifest_path in manifests:
        manifest_dir, _ = split_manifest_path(manifest_path)
        current_dir = manifest_dir
        skip_nested_manifest = False

        while current_dir:
            parent_dir, _ = split_manifest_path(current_dir)
            if current_dir in manifest_dirs:
                skip_nested_manifest = True
                break
            current_dir = parent_dir

        if skip_nested_manifest:
            continue

        filtered_manifests.append(manifest_path)
        manifest_dirs.add(manifest_dir)

    return filtered_manifests


def parse_package_manifest(xml_text: str) -> Optional[Tuple[str, str]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag != "package":
        return None

    name = root.findtext("name")
    version = root.findtext("version")
    if not name or not version:
        return None
    return name.strip(), version.strip()


def collect_package_entries(client: GitHubClient, repo: dict) -> List[PackageEntry]:
    org = repo["owner"]["login"]
    repo_ref = select_repo_ref(client, repo)
    paths = list_tree_paths(client, repo, repo_ref.ref)
    manifests = discover_manifest_paths(paths)
    entries: List[PackageEntry] = []

    for manifest_path in manifests:
        try:
            xml_text = fetch_text_file(client, org, repo_ref.name, repo_ref.ref, manifest_path)
        except urllib.error.HTTPError as exc:
            print(
                f"Skipping {repo_ref.name}:{manifest_path} because the manifest could not be fetched: {exc}",
                file=sys.stderr,
            )
            continue

        parsed = parse_package_manifest(xml_text)
        if not parsed:
            continue

        package_name, version = parsed
        entries.append(
            PackageEntry(
                package_name=package_name,
                version=version,
                repo_name=repo_ref.name,
                clone_url=repo_ref.clone_url,
                ref=repo_ref.ref,
                ref_key=repo_ref.ref_key,
                manifest_path=manifest_path,
            )
        )

    return entries


def yaml_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_yaml(entries: List[PackageEntry], org: str) -> str:
    lines = [
        "# This file is generated by scrape_isaac_ros_additional_recipes.py.",
        f"# Source organization: {org}",
        "# Review entries before copying them into rosdistro_additional_recipes.yaml.",
        "",
    ]

    for entry in sorted(entries, key=lambda item: (item.repo_name, item.package_name)):
        lines.append(f"{entry.package_name}:")
        lines.append(f"  {entry.ref_key}: {yaml_quote(entry.ref)}")
        lines.append(f"  url: {yaml_quote(entry.clone_url)}")
        lines.append(f"  version: {yaml_quote(entry.version)}")

        folder, filename = split_manifest_path(entry.manifest_path)
        if folder:
            lines.append(f"  additional_folder: {yaml_quote(folder)}")
        if filename != "package.xml":
            lines.append(f"  package_xml_name: {yaml_quote(filename)}")

    return "\n".join(lines).rstrip() + "\n"


def render_vinca_yaml(entries: List[PackageEntry], org: str) -> str:
    lines = [
        "# This file is generated by scrape_isaac_ros_additional_recipes.py.",
        f"# Source organization: {org}",
        "# Copy the package names below into vinca.yaml under packages_select_by_deps.",
        "# Example:",
        "#   - if: linux",
        "#     then:",
        "",
    ]

    repo_names = []
    seen_repos = set()
    for entry in entries:
        if entry.repo_name not in seen_repos:
            seen_repos.add(entry.repo_name)
            repo_names.append(entry.repo_name)

    grouped_entries = sorted(entries, key=lambda item: (repo_names.index(item.repo_name), item.package_name))
    current_repo = None
    for entry in grouped_entries:
        if entry.repo_name != current_repo:
            current_repo = entry.repo_name
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"# {current_repo}")
        lines.append(f"- {entry.package_name}")

    return "\n".join(lines).rstrip() + "\n"


def split_manifest_path(path: str) -> Tuple[str, str]:
    if "/" not in path:
        return "", path
    folder, filename = path.rsplit("/", 1)
    return folder, filename


def dedupe_entries(entries: Iterable[PackageEntry]) -> Tuple[List[PackageEntry], List[str]]:
    by_name: Dict[str, PackageEntry] = {}
    warnings: List[str] = []
    for entry in entries:
        existing = by_name.get(entry.package_name)
        if existing is None:
            by_name[entry.package_name] = entry
            continue

        warnings.append(
            "Duplicate package name "
            f"{entry.package_name!r} found in {existing.repo_name} and {entry.repo_name}; "
            f"keeping {existing.repo_name}."
        )
    return list(by_name.values()), warnings


def main() -> int:
    args = parse_args()
    token = os.environ.get(args.token_env)
    client = GitHubClient(token=token, pause_seconds=args.pause_seconds)

    all_entries: List[PackageEntry] = []
    repo_names = list(TARGET_REPOSITORIES)
    print(f"Inspecting {len(repo_names)} curated repositories from {args.org}...", file=sys.stderr)

    for index, repo_name in enumerate(repo_names, start=1):
        print(f"[{index}/{len(repo_names)}] {repo_name}", file=sys.stderr)
        try:
            repo = get_repo(client, args.org, repo_name)
            entries = collect_package_entries(client, repo)
            all_entries.extend(entries)
        except RuntimeError as exc:
            print(f"Skipping repo {repo_name}: {exc}", file=sys.stderr)

    entries, warnings = dedupe_entries(all_entries)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    output = render_yaml(entries, args.org)
    with open(args.output, "w", encoding="utf-8") as stream:
        stream.write(output)

    vinca_output = render_vinca_yaml(entries, args.org)
    with open(args.vinca_output, "w", encoding="utf-8") as stream:
        stream.write(vinca_output)

    print(f"Wrote {len(entries)} package entries to {args.output}", file=sys.stderr)
    print(f"Wrote vinca package list to {args.vinca_output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
