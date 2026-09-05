"""Co dung chung giua job.py va stages.py: DUNG KHI NAO.

Hai nguon lam job dung som, va ca hai deu phai dung SACH — commit lo dang lam roi
thoat voi ma 0:

  1. SIGTERM. K8s gui khi evict/scale, va khi may shutdown.
  2. HET NGAN SACH THOI GIAN (MAX_MINUTES), xem set_budget().

Vi sao can module rieng: job.py bat SIGTERM, nhung vong lap that su nam trong
stages.py. Truoc day co _stop cuc bo trong job.py va chi kiem tra GIUA cac stage,
nen SIGTERM giua mot stage khong co tac dung — pod chay tiep den khi bi SIGKILL,
mat lo dang do va de lai dong fp_run mo.

## Vi sao job phai tu dat tran thay vi de activeDeadlineSeconds cat

Kubernetes khong co khai niem "con lam duoc viec thi dung cat". Vong doi mot Job bi
chan boi dung ba thu — activeDeadlineSeconds (dong ho tuong), backoffLimit, va
process exit 0 — khong cai nao hoi den suc khoe hay tien do. livenessProbe co dung
duoc trong pod cua Job nhung chi lam no chet SOM hon, khong bao gio giu no song lau
hon. Va health check Lua cua ArgoCD chi la BAO CAO cho UI, no khong noi gi voi
Kubernetes.

Nen cach duy nhat de "dung dung luc" la job tu biet gio. Doi DeadlineExceeded
thanh exit 0 sua bon thu cung luc:

  1. Job -> Succeeded, ArgoCD thoi bao Degraded moi sang.
  2. status.lastSuccessfulTime cua CronJob duoc cap nhat.
  3. LOG CON LAI. Job fail thi controller xoa pod (restartPolicy: OnFailure),
     kubelet don /var/log/pods, log bay mat — dung cai da che 13 dem OOMKilled.
  4. Khoi finally cua run_log() chay -> khong con dong fp_run bo lung, nen
     /api/progress thoi hien mot stage "dang chay" khi khong con pod nao.

Dat MAX_MINUTES thap hon activeDeadlineSeconds mot khoang an toan; deadline cua k8s
tro thanh luoi cho truong hop job treo THAT, dung vai cua no.
"""
import time

_stop = False
_reason = ""
# Moc time.monotonic() phai dung truoc. monotonic chu khong phai time(): dong ho
# he thong co the nhay (NTP) va phep do khoang thoi gian thi khong duoc phep nhay.
_deadline = None
_announced = False


def request_stop(reason="tin hieu"):
    global _stop, _reason
    _stop = True
    _reason = reason


def set_budget(minutes):
    """Tu dat tran thoi gian chay, tinh bang phut. 0 hoac None = khong gioi han."""
    global _deadline, _announced
    _announced = False
    try:
        m = float(minutes or 0)
    except (TypeError, ValueError):
        m = 0.0
    _deadline = (time.monotonic() + m * 60.0) if m > 0 else None
    return _deadline is not None


def should_stop():
    """True khi phai dung. stages.py goi ham nay ngay SAU moi lan commit.

    Ngan sach het duoc doi thanh mot lan dung binh thuong, nen moi vong lap trong
    stages.py xu ly no bang dung duong da co san: break, roi thoat sach.
    """
    global _stop, _reason, _announced
    if _stop:
        return True
    if _deadline is not None and time.monotonic() >= _deadline:
        if not _announced:
            _announced = True
            # In mot lan duy nhat o day, thay vi sua thong bao o chin cho goi:
            # doc log se thay ro ly do ngay truoc dong "thoat sau khi da commit".
            print("\n[ngan sach] het thoi gian cho phep, dung sach sau khi commit "
                  "lo hien tai. Lan chay sau tiep tuc dung cho nay.")
        _stop = True
        _reason = "het ngan sach thoi gian"
        return True
    return False


def stop_reason():
    return _reason


def remaining_minutes():
    """Con bao nhieu phut, hoac None neu khong dat ngan sach."""
    if _deadline is None:
        return None
    return max(0.0, (_deadline - time.monotonic()) / 60.0)


def reset():
    global _stop, _reason, _deadline, _announced
    _stop = False
    _reason = ""
    _deadline = None
    _announced = False
