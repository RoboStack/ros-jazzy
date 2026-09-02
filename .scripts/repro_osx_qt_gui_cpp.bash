#!/usr/bin/env bash
set -uo pipefail

# Reproduce the osx-arm64 qt_gui_cpp SIP/qmake failure seen in:
# https://github.com/RoboStack/ros-jazzy/actions/runs/33638131168/job/100274063898
#
# The failing generated GHA job built this batch on macos-15:
#   ros2-rosidl-typesupport-fastrtps-cpp
#   ros2-rosidl-typesupport-introspection-cpp
#   ros2-rcl-yaml-param-parser
#   ros2-urdf
#   ros2-qt-gui-cpp
# and failed while building ros2-qt-gui-cpp with:
#   clang++: error: invalid version number in '-mmacosx-version-min='

TARGET_PLATFORM="${TARGET_PLATFORM:-osx-arm64}"
CONDA_BLD_PATH="${CONDA_BLD_PATH:-$HOME/conda-bld}"
ARTIFACT_DIR="${ARTIFACT_DIR:-repro-artifacts}"
GENERATE_RECIPES="${GENERATE_RECIPES:-true}"
RECIPES=("${@:-ros2-rosidl-typesupport-fastrtps-cpp ros2-rosidl-typesupport-introspection-cpp ros2-rcl-yaml-param-parser ros2-urdf ros2-qt-gui-cpp}")

# If no positional args were provided, split the default string into an array.
if [[ $# -eq 0 ]]; then
  read -r -a RECIPES <<< "${RECIPES[*]}"
fi

export PYTHONUNBUFFERED=1
export FEEDSTOCK_ROOT="$(pwd)"
export CONDA_BLD_PATH

mkdir -p "$CONDA_BLD_PATH" "$ARTIFACT_DIR"

log_section() {
  echo
  echo "::group::$*"
}

end_section() {
  echo "::endgroup::"
}

collect_recipe_artifacts() {
  local recipe="$1"
  local dest="$ARTIFACT_DIR/$recipe"
  mkdir -p "$dest"

  {
    echo "recipe=$recipe"
    echo "target=$TARGET_PLATFORM"
    echo "CONDA_BLD_PATH=$CONDA_BLD_PATH"
    echo "date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    env | sort | grep -E '^(BUILD_|CONDA_|CMAKE_|MACOSX_|OSX_|SDKROOT|QMAKE|QT_|PYTHON|RATTLER|GITHUB_)' || true
  } > "$dest/environment-summary.txt"

  # Capture the rattler-build work directory and the files most useful for the
  # qmake/SIP deployment-target failure. Use find because rattler-build embeds a
  # timestamp in the work directory name.
  while IFS= read -r -d '' work_dir; do
    local stamp
    stamp="$(basename "$(dirname "$work_dir")")"
    local out="$dest/$stamp"
    mkdir -p "$out"
    echo "$work_dir" > "$out/work-dir.txt"

    find "$work_dir" -maxdepth 8 \( \
      -name 'conda_build.log' -o \
      -name 'build_env.sh' -o \
      -name '.source_info.json' -o \
      -name 'CMakeCache.txt' -o \
      -name 'pyproject.toml' -o \
      -name '*.pro' -o \
      -name 'Makefile' \
    \) -print0 | while IFS= read -r -d '' file; do
      local rel
      rel="${file#$work_dir/}"
      mkdir -p "$out/$(dirname "$rel")"
      cp "$file" "$out/$rel" || true
    done

    while IFS= read -r -d '' helper_file; do
      local rel
      rel="${helper_file#$(dirname "$work_dir")/}"
      mkdir -p "$out/host-helper/$(dirname "$rel")"
      cp "$helper_file" "$out/host-helper/$rel" || true
    done < <(find "$(dirname "$work_dir")" -path '*/share/python_qt_binding/cmake/*' -type f -print0 2>/dev/null)

    {
      echo "# grep: deployment target flags"
      grep -RIn -- '-mmacosx-version-min\|QMAKE_MACOSX_DEPLOYMENT_TARGET\|minimum-macos-version\|MACOSX_DEPLOYMENT_TARGET\|CMAKE_OSX_DEPLOYMENT_TARGET' "$work_dir" "$(dirname "$work_dir")"/host_env*/share/python_qt_binding/cmake 2>/dev/null || true
    } > "$out/deployment-target-grep.txt"
  done < <(find "$CONDA_BLD_PATH" -path "*/rattler-build_${recipe}_*/work" -type d -print0 2>/dev/null)
}

log_section "Host diagnostics"
echo "TARGET_PLATFORM=$TARGET_PLATFORM"
echo "CONDA_BLD_PATH=$CONDA_BLD_PATH"
echo "ARTIFACT_DIR=$ARTIFACT_DIR"
echo "RECIPES=${RECIPES[*]}"
uname -a || true
sw_vers || true
xcodebuild -version || true
xcrun --show-sdk-path || true
env | sort | grep -E '^(BUILD_|CONDA_|CMAKE_|MACOSX_|OSX_|SDKROOT|QMAKE|QT_|PYTHON|RATTLER|GITHUB_)' || true
end_section

if [[ "$TARGET_PLATFORM" == osx* ]]; then
  # Match .scripts/build_unix.sh and avoid Homebrew tools leaking into the build.
  export PATH="$(echo "$PATH" | tr ':' '\n' | grep -v 'homebrew' | paste -sd ':' -)"
fi

if [[ "$GENERATE_RECIPES" == "true" ]]; then
  log_section "Generate recipes for $TARGET_PLATFORM"
  pixi run -v vinca --platform "$TARGET_PLATFORM" -m -n
  gen_status=$?
  end_section
  if [[ $gen_status -ne 0 ]]; then
    echo "Recipe generation failed with status $gen_status"
    exit "$gen_status"
  fi
fi

log_section "Generated recipe and patch inspection"
for recipe in "${RECIPES[@]}"; do
  echo "## $recipe"
  if [[ -f "recipes/$recipe/recipe.yaml" ]]; then
    grep -nE 'name:|version:|number:|patches:|ros2-python-qt-binding|python_qt_binding|MACOSX|CMAKE_OSX|qt-gui-cpp' "recipes/$recipe/recipe.yaml" || true
  else
    echo "recipes/$recipe/recipe.yaml is missing"
  fi
  find "recipes/$recipe/patch" -maxdepth 1 -type f -print -exec grep -nE 'CMAKE_OSX_DEPLOYMENT_TARGET|MACOSX_DEPLOYMENT_TARGET|OSX_DEPLOYMENT_TARGET|build_sip_binding|minimum-macos-version|QMAKE_MACOSX_DEPLOYMENT_TARGET' {} \; 2>/dev/null || true
  echo
done
end_section

status=0
for recipe in "${RECIPES[@]}"; do
  log_section "Build $recipe"
  pixi run -v rattler-build build \
    --recipe "$FEEDSTOCK_ROOT/recipes/$recipe" \
    --target-platform "$TARGET_PLATFORM" \
    -m "$FEEDSTOCK_ROOT/conda_build_config.yaml" \
    -c robostack-jazzy -c conda-forge \
    --output-dir "$CONDA_BLD_PATH"
  status=$?
  end_section

  collect_recipe_artifacts "$recipe"

  if [[ $status -ne 0 ]]; then
    echo "Build failed for $recipe with status $status"
    break
  fi
done

log_section "Collected repro artifacts"
find "$ARTIFACT_DIR" -maxdepth 4 -type f | sort || true
end_section

exit "$status"
