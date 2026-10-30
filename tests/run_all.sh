#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Eddy Moncada Garcia
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Runs every headless test in parallel batches and prints a summary. Each test writes its log to tests/out/.
#   BLENDER=/path/to/blender bash tests/run_all.sh
# Passing: every "FAILURES: none", 0 tracebacks, and nothing listed under NONMANIFOLD (Suzanne/GUIDE pieces are
# expected to be non-manifold).
set -u
B="${BLENDER:?set BLENDER to the Blender executable}"
cd "$(dirname "$0")"
mkdir -p out
USER_SCRIPTS="$(mktemp -d)"
NATIVE_USER_SCRIPTS="$USER_SCRIPTS"
command -v cygpath > /dev/null && NATIVE_USER_SCRIPTS="$(cygpath -w "$USER_SCRIPTS")"  # Git Bash on Windows
EMPTY_SCRIPTS="$(mktemp -d)"  # test_register: a new user with no presets of their own
NATIVE_EMPTY_SCRIPTS="$EMPTY_SCRIPTS"
command -v cygpath > /dev/null && NATIVE_EMPTY_SCRIPTS="$(cygpath -w "$EMPTY_SCRIPTS")"
run() { timeout 1500 "$B" -b --factory-startup --python "$1" > "out/$2" 2>&1; }

run test_preview.py preview_out.txt &
run test_layout_edit.py layout_edit_out.txt &
run test_cache.py cache_out.txt &
run test_cutface.py cutface_out.txt &
run test_parting.py parting_out.txt &
run test_gate.py gate_out.txt &
run test_core.py core_out.txt &
run test_shading.py shading_out.txt &
run test_tips.py tips_out.txt &
run test_marker_shapes.py markers_out.txt &
wait
run test_export.py export_out.txt &
run test_bed.py bed_out.txt &
BLENDER_USER_SCRIPTS="$NATIVE_USER_SCRIPTS" timeout 1500 "$B" -b --factory-startup --python test_medium.py > out/medium_out.txt 2>&1 &
run test_two_part.py two_out.txt &
wait
run test_tray.py tray_out.txt &
run test_new.py new_out.txt &
run test_layout.py layout_out.txt &
run test_features.py feat_out.txt &
BLENDER_USER_SCRIPTS="$NATIVE_EMPTY_SCRIPTS" timeout 1500 "$B" -b --factory-startup --python test_register.py > out/register_out.txt 2>&1 &
wait
run test_clamps.py clamps_out.txt &
run test_clamping.py clamping_out.txt &
run test_review_fixes.py review_out.txt &
wait
rm -rf "$USER_SCRIPTS" "$EMPTY_SCRIPTS"

cd out
for f in markers tips shading cutface cache layout_edit preview parting gate core export bed medium two tray register review; do
    echo "${f}_out.txt: $(grep -h FAILURES "${f}_out.txt" 2>/dev/null || echo 'NO RESULT')"
done
for f in new layout feat clamps clamping; do
    echo "${f}_out.txt: $(grep -c FINISHED "${f}_out.txt") finished, $(grep -c Traceback "${f}_out.txt") tracebacks"
done
echo "NONMANIFOLD:"; grep -E "nonmanifold=\s+[1-9]" *_out.txt | grep -vE "Suzanne|GUIDE"
echo done
