#!/usr/bin/env bats

setup_file() {
  export TESTS_DIR="/tmp/tests-`openssl rand -hex 16`"
  export TEST_DS1="DS01"
  export TEST_XP="XP01"
  # create a dedicated workspace for the tests
  echo -en "$TESTS_DIR" > ~/.packing-box/experiments.env
  run experiment open "$TEST_XP"
}

setup() {
  load '.bats/bats-support/load'
  load '.bats/bats-assert/load'
  load '.bats/bats-file/load'
  load '.bats/pbox-helpers/load'
}

teardown_file(){
  # clean up the dedicated workspace
  run experiment close
  rm -f ~/.packing-box/experiments.env
  rm -rf "$TESTS_DIR"
}

@test "run tool's help" {
  run_tool_help
}

@test "detect samples from $TEST_DS1" {
  dataset make $TEST_DS1 -f PE -p upx -n 10
  for DETECTOR in `list-working-detectors`; do
    run detector $TEST_DS1 -d $DETECTOR
    assert_success
  done
}

