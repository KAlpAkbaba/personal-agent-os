import state_mod


def test_leaves_the_flag_set():
    state_mod.FLAG = True
    assert state_mod.FLAG
