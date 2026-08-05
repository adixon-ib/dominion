#!/bin/bash
# Interactive test runner for nios_test

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

tests=(
    "test_transport.py"
    "test_session.py"
    "test_container.py"
    "test_appliance.py"
    "test_deploy.py"
    "local_appliance.py"
    "test_grid.py"
    "test_sc_hello.py"
)

PS3=$'\nSelect test: '
echo ""
select t in "${tests[@]}" "Quit"; do
    case "$t" in
        "Quit") break ;;
        "")     echo "Invalid choice" ;;
        *)      python3 "$TESTS_DIR/$t"; break ;;
    esac
done
