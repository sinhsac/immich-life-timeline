"""Co dung chung giua job.py va stages.py.

Vi sao can module rieng: job.py bat SIGTERM, nhung vong lap that su nam trong
stages.py. Truoc day co _stop cuc bo trong job.py va chi kiem tra GIUA cac
stage, nen SIGTERM giua mot stage khong co tac dung — pod chay tiep den khi bi
SIGKILL, mat lo dang do va de lai dong fp_run mo.

Dat co o day de stages.py kiem tra sau moi lan commit.
"""
_stop = False


def request_stop():
    global _stop
    _stop = True


def should_stop():
    return _stop


def reset():
    global _stop
    _stop = False
