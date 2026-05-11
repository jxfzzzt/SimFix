#!/usr/bin/env bash
# build.sh - Build an executable simfix.jar from src/ using JDK 11.
#
# The SimFix source code (developed for JDK 1.7) contains a number of Eclipse
# auto-imported but unused JDK-internal/javax-removed-in-JDK11 symbols. Those
# imports compile fine in Eclipse JDT but break javac on JDK 11. This script
# strips those imports from a clean copy of src/, then compiles with
# `javac --release 7` and packages the result as a runnable jar.
#
# Usage:
#   ./build.sh [--clean] [--skip-data] [--skip-junit-copy]
#
# The produced jar must be launched from the SimFix project root because
# cofix.common.config.Constant uses System.getProperty("user.dir") to locate
# d4j-info/, log/, patch/, sbfl/.

set -euo pipefail

# ---------- locate project root ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

CLEAN=0
SKIP_DATA=0
SKIP_JUNIT_COPY=0
for arg in "$@"; do
    case "${arg}" in
        --clean)           CLEAN=1 ;;
        --skip-data)       SKIP_DATA=1 ;;
        --skip-junit-copy) SKIP_JUNIT_COPY=1 ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        *)
            echo "[build.sh] unknown argument: ${arg}" >&2
            exit 2
            ;;
    esac
done

log()  { printf '\033[1;34m[build.sh]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[build.sh][warn]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[build.sh][error]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- check JDK ----------
command -v java  >/dev/null || fail "java not found in PATH"
command -v javac >/dev/null || fail "javac not found in PATH"
command -v jar   >/dev/null || fail "jar not found in PATH"

JAVA_VERSION_RAW="$(java -version 2>&1 | head -n1)"
log "java -version : ${JAVA_VERSION_RAW}"
if ! echo "${JAVA_VERSION_RAW}" | grep -qE '"11\.|"1\.11'; then
    warn "Detected Java is not 11.x; proceeding anyway. If javac --release 7 fails, switch to JDK 11."
fi

# ---------- step 1: unzip sbfl/data.zip if needed ----------
if [[ ${SKIP_DATA} -eq 0 ]]; then
    if [[ -d sbfl/data && -n "$(ls -A sbfl/data 2>/dev/null || true)" ]]; then
        log "sbfl/data/ already exists, skip unzip"
    elif [[ -f sbfl/data.zip ]]; then
        log "unzipping sbfl/data.zip -> sbfl/data/"
        ( cd sbfl && unzip -q -o data.zip )
    else
        warn "sbfl/data.zip not found; fault localization data may be missing"
    fi
fi

# ---------- step 2: copy junit-hamcrest jar from defects4j ----------
if [[ ${SKIP_JUNIT_COPY} -eq 0 ]]; then
    D4J_HOME_GUESS="${DEFECTS4J_HOME:-/Users/zhouzhuotong/defects4j}"
    JUNIT_SRC="${D4J_HOME_GUESS}/framework/projects/lib/junit-4.12-hamcrest-1.3.jar"
    JUNIT_DST="lib/junit-4.12-hamcrest-1.3.jar"
    if [[ -f "${JUNIT_DST}" ]]; then
        log "${JUNIT_DST} already present"
    elif [[ -f "${JUNIT_SRC}" ]]; then
        log "copying junit jar from defects4j: ${JUNIT_SRC} -> ${JUNIT_DST}"
        cp "${JUNIT_SRC}" "${JUNIT_DST}"
    else
        warn "junit jar not found at ${JUNIT_SRC}; org.junit.runner.Result reference will fail to compile"
        warn "set DEFECTS4J_HOME or drop a junit-4.x jar into lib/ before retrying"
    fi
fi

# ---------- step 3: prepare clean src tree ----------
BUILD_DIR="build"
SRC_CLEAN="${BUILD_DIR}/src-clean"
CLASSES_DIR="${BUILD_DIR}/classes"
MANIFEST_FILE="${BUILD_DIR}/MANIFEST.MF"
SOURCES_LIST="${BUILD_DIR}/sources.txt"

if [[ ${CLEAN} -eq 1 ]]; then
    log "cleaning ${BUILD_DIR}/ and simfix.jar"
    rm -rf "${BUILD_DIR}" simfix.jar
fi

rm -rf "${SRC_CLEAN}" "${CLASSES_DIR}"
mkdir -p "${SRC_CLEAN}" "${CLASSES_DIR}"

log "mirroring src/ -> ${SRC_CLEAN}/"
cp -R src/. "${SRC_CLEAN}/"

# ---------- step 4: strip JDK-internal / removed unused imports ----------
BAD_IMPORTS=(
    "import sun.security.provider.MD2;"
    "import sun.security.x509.UniqueIdentity;"
    "import sun.management.counter.Units;"
    "import com.sun.org.apache.xalan.internal.xsltc.compiler.NodeTest;"
    "import com.sun.org.apache.xpath.internal.SourceTreeManager;"
    "import com.sun.org.apache.xpath.internal.operations.Mod;"
    "import com.sun.corba.se.spi.ior.TaggedProfileTemplate;"
    "import com.sun.xml.internal.bind.v2.runtime.Name;"
    "import com.sun.org.apache.bcel.internal.classfile.Code;"
    "import javax.jws.WebParam.Mode;"
    "import javax.print.attribute.standard.MediaSize.Other;"
)

log "stripping ${#BAD_IMPORTS[@]} bad imports from ${SRC_CLEAN}/"
# Write the bad imports to a file then use grep -vxFf to strip exact matching
# lines from every .java file. -x = whole-line match, -F = fixed strings.
BAD_IMPORTS_FILE="${BUILD_DIR}/bad_imports.txt"
printf '%s\n' "${BAD_IMPORTS[@]}" > "${BAD_IMPORTS_FILE}"
while IFS= read -r -d '' jf; do
    if grep -qxFf "${BAD_IMPORTS_FILE}" "${jf}"; then
        grep -vxFf "${BAD_IMPORTS_FILE}" "${jf}" > "${jf}.tmp"
        mv "${jf}.tmp" "${jf}"
    fi
done < <(find "${SRC_CLEAN}" -name "*.java" -print0)

# ---------- step 5: collect sources & compile ----------
find "${SRC_CLEAN}" -name "*.java" > "${SOURCES_LIST}"
NUM_SRC=$(wc -l < "${SOURCES_LIST}" | tr -d ' ')
log "compiling ${NUM_SRC} java sources with javac --release 7"

CP="lib/*"
set +e
javac --release 7 -encoding UTF-8 -nowarn -Xlint:none \
      -cp "${CP}" -d "${CLASSES_DIR}" @"${SOURCES_LIST}" \
      2> "${BUILD_DIR}/javac.err"
JAVAC_RC=$?
set -e

if [[ ${JAVAC_RC} -ne 0 ]]; then
    warn "javac exited with code ${JAVAC_RC}; last 30 lines of stderr:"
    tail -n 30 "${BUILD_DIR}/javac.err" >&2
    NUM_CLASS=$(find "${CLASSES_DIR}" -name "*.class" | wc -l | tr -d ' ')
    if [[ ${NUM_CLASS} -eq 0 ]]; then
        fail "no class files produced, aborting"
    fi
    warn "compilation produced ${NUM_CLASS} class files, continuing to package"
else
    log "javac OK"
fi

# ---------- step 6: write MANIFEST ----------
log "writing ${MANIFEST_FILE}"
# JAR Manifest spec: each line is at most 72 bytes; longer values continue on
# subsequent lines that MUST start with a single space.
CP_ENTRIES=()
for j in lib/*.jar; do
    [[ -f "$j" ]] && CP_ENTRIES+=("$j")
done
CP_VALUE="${CP_ENTRIES[*]}"

{
    echo "Manifest-Version: 1.0"
    echo "Created-By: SimFix build.sh"
    echo "Main-Class: cofix.main.Main"
    if [[ -n "${CP_VALUE}" ]]; then
        # Build "Class-Path: <value>", then fold to <=72 bytes per physical line.
        printf 'Class-Path: %s' "${CP_VALUE}" | awk '
            {
                line = $0
                while (length(line) > 72) {
                    print substr(line, 1, 72)
                    line = " " substr(line, 73)
                }
                print line
            }
        '
    fi
} > "${MANIFEST_FILE}"
# Ensure the file ends with a blank line per the spec
echo "" >> "${MANIFEST_FILE}"

# ---------- step 7: build jar ----------
log "building simfix.jar"
jar cfm simfix.jar "${MANIFEST_FILE}" -C "${CLASSES_DIR}" .

# ---------- step 8: smoke test ----------
log "smoke test: java -jar simfix.jar (expect usage)"
set +e
SMOKE_OUT="$(java -jar simfix.jar 2>&1)"
SMOKE_RC=$?
set -e
echo "${SMOKE_OUT}" | sed 's/^/    /'
if echo "${SMOKE_OUT}" | grep -q "Usage : --proj_home"; then
    log "build succeeded: simfix.jar"
else
    warn "smoke test did not print expected usage (rc=${SMOKE_RC})."
    warn "this can still be acceptable -- inspect output above."
fi

log "done."
