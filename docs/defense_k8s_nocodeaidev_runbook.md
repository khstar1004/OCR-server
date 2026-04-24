# 국방망 k8s 배포 가이드: app 공개 + OCR API 공개

이 문서는 RedHat Linux, `nocodeaidev` 네임스페이스, `nginx` IngressClass, `local-path` StorageClass, `harbor-reg-cred` imagePullSecret, HAMi GPU 분할 환경 기준입니다.

## 최종 구조

- Docker Compose는 병행하지 않습니다. k8s/HAMi가 GPU를 관리하는 환경에서는 compose가 GPU를 직접 잡으면 스케줄링 상태와 실제 GPU 점유가 어긋날 수 있습니다.
- `app`은 외부 공개합니다.
- `ocr-service`도 외부 공개합니다.
- `vllm-ocr`는 절대 외부 공개하지 않습니다. OCR API 뒤의 내부 추론 엔진으로만 둡니다.
- 외부 app URL: `https://nocodeaidev.army.mil:20443/a-cong-ocr`
- 외부 OCR API URL: `https://nocodeaidev.army.mil:20443/a-cong-ocr-api`
- k8s Ingress YAML에는 `:20443`을 쓰지 않습니다. `host: nocodeaidev.army.mil`과 `path`만 씁니다.

공개 API 예시:

```text
GET  https://nocodeaidev.army.mil:20443/a-cong-ocr/api/v1/health
GET  https://nocodeaidev.army.mil:20443/a-cong-ocr/demo/jobs
GET  https://nocodeaidev.army.mil:20443/a-cong-ocr-api/health
GET  https://nocodeaidev.army.mil:20443/a-cong-ocr-api/api/v1/health
POST https://nocodeaidev.army.mil:20443/a-cong-ocr-api/api/v1/ocr/image
POST https://nocodeaidev.army.mil:20443/a-cong-ocr-api/api/v1/ocr
POST https://nocodeaidev.army.mil:20443/a-cong-ocr-api/api/v1/marker
```

## 반입 폴더

이번 작업에서 정리한 반입 폴더:

```text
dist/defense-k8s-public-ocr-carry-in/
```

필수 파일:

```text
START_HERE_PUBLIC_K8S.txt
k8s/defense-remote-ocr.nocodeaidev.yaml
docs/defense_k8s_nocodeaidev_runbook.md
scripts/check_k8s_public_ocr.sh
scripts/validate_vllm_image_offline.sh
dist/a-cong-ocr_chandra.tar
dist/a-cong-vllm-openai_chandra.tar
news_models/chandra-ocr-2/
```

이미 서버에 `/data/news_models/chandra-ocr-2` 모델이 확실히 있으면 `news_models/chandra-ocr-2/` 반입은 생략할 수 있습니다. 완전 폐쇄망 재현성을 원하면 모델까지 같이 들고 가는 쪽이 안전합니다.

## 가장 중요한 vLLM 폐쇄망 원칙

`trust_remote_code`만 추가한다고 항상 해결되지 않습니다.

폐쇄망에서는 transformers/vLLM이 인터넷에서 부족한 모델 코드를 내려받을 수 없습니다. 따라서 아래 3개가 반드시 한 묶음으로 맞아야 합니다.

- 실제 사용할 `chandra-ocr-2` 모델 폴더
- 그 모델을 인식하는 `transformers`, `tokenizers`, `huggingface_hub`, `vllm`가 들어간 vLLM 이미지
- vLLM 실행 인자 `--trust-remote-code`

이번 매니페스트와 vLLM entrypoint는 다음을 보장합니다.

- `--trust-remote-code`를 항상 붙입니다.
- `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`로 폐쇄망 동작을 강제합니다.
- `VLLM_EXPECT_MODEL_TYPE`을 빈 값으로 두면 `/models/chandra-ocr-2/config.json`의 `model_type`을 자동 감지합니다.
- `qwen2_5`, `qwen3_5`처럼 현장 모델이 달라도 하드코딩된 model type 때문에 먼저 죽지 않습니다.
- 이미지 시작 시 `qwen2_5 -> qwen2`, `qwen2_5_text -> qwen2` 호환 alias를 자동 등록합니다.
- 포함된 Chandra 모델은 top-level `qwen3_5`이지만, `Qwen2VLImageProcessorFast`, `Qwen2Tokenizer`도 같이 쓰므로 둘 다 검증합니다.
- 실제 모델 폴더를 `AutoConfig`, `AutoProcessor`, vLLM config로 먼저 검증한 뒤 `vllm serve`를 실행합니다.

그래도 `transformers does not recognize this architecture`가 뜨면, 그 이미지는 그 모델을 지원하지 않는 이미지입니다. 폐쇄망에서 고칠 수 있는 문제가 아니라, 인터넷 준비 PC에서 성공한 vLLM 이미지를 다시 만들어서 `docker save`로 반입해야 합니다.

이번 반입 이미지의 검증 범위:

- `docker run --network none` 상태에서 실제 `chandra-ocr-2` 모델 폴더 로딩 성공
- `qwen2`, `qwen2_5`, `qwen2_vl`, `qwen2_5_vl`, `qwen2_5_text`, `qwen3`, `qwen3_5`, `qwen3_5_text` 매핑 확인
- `AutoConfig`, `AutoProcessor`, `AutoTokenizer`, vLLM config 로딩 성공
- `--network none`, NVIDIA runtime, 실제 모델 폴더, `vllm serve`, `/health` 성공

## 1. 현장 값 확인

```bash
kubectl get ingressclass
kubectl get storageclass
kubectl -n nocodeaidev get secret harbor-reg-cred
kubectl get nodes -o wide
kubectl describe node nocode-ai-army01 | egrep 'nvidia.com/(gpu|gpumem|gpucores)|Allocatable|Capacity'
```

판단:

- `ingressclass`는 `nginx`여야 합니다. 다르면 매니페스트의 `ingressClassName`을 바꿉니다.
- `storageclass`는 현재 확인된 `local-path` 기준입니다. 다르면 PVC의 `storageClassName`을 바꿉니다.
- `harbor-reg-cred`가 `nocodeaidev` 네임스페이스에 있어야 합니다.
- `nvidia.com/gpu` Capacity가 `40`이고 현재 사용량이 충분하면 기본값 `nvidia.com/gpu: "1"`로 시작합니다.
- `nvidia.com/gpumem` 또는 `nvidia.com/gpumem-percentage`가 Allocatable에 보일 때만 gpumem 설정을 켭니다.
- `gpumem` 단위는 `45`가 아니라 보통 `45000` 또는 `50000` 수준입니다.

## 2. 인터넷 준비 PC에서 이미지 재생성

이번 코드 변경에는 k8s 서브패스와 vLLM 검증 변경이 들어갔습니다. 기존 tar를 그대로 쓰면 변경이 반영되지 않을 수 있습니다. 반드시 새로 빌드해서 tar를 다시 만드세요.

```bash
docker build -t nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr:chandra .
docker build -f Dockerfile.vllm -t nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra .
```

인터넷 준비 PC에서 실제 모델 폴더를 붙여 vLLM 이미지를 검증합니다.

```bash
chmod +x scripts/validate_vllm_image_offline.sh
scripts/validate_vllm_image_offline.sh \
  nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra \
  ./news_models/chandra-ocr-2
```

이 단계가 통과하지 않으면 폐쇄망에 가져가도 실패합니다. 특히 여기서 `transformers does not recognize this architecture`가 나오면 vLLM 이미지를 다시 만들어야 합니다.

검증 후 저장합니다.

```bash
mkdir -p dist
docker save -o dist/a-cong-ocr_chandra.tar nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr:chandra
docker save -o dist/a-cong-vllm-openai_chandra.tar nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra
```

## 3. 폐쇄망에서 한 번에 배포하기

반입 폴더를 `/opt/defense-k8s-public-ocr-carry-in`에 복사했다고 가정합니다.

```bash
cd /opt/defense-k8s-public-ocr-carry-in
chmod +x scripts/*.sh
./scripts/deploy_public_ocr_closed_network.sh
```

callback 대상 값을 같이 넣어야 하면:

```bash
cd /opt/defense-k8s-public-ocr-carry-in
chmod +x scripts/*.sh
TARGET_API_BASE_URL='http://<대상서버>:<PORT>/news' \
TARGET_API_TOKEN='<token>' \
./scripts/deploy_public_ocr_closed_network.sh
```

Harbor에 이미 이미지가 올라가 있어 push를 건너뛰려면:

```bash
SKIP_HARBOR_PUSH=1 ./scripts/deploy_public_ocr_closed_network.sh
```

이 스크립트가 하는 일:

- k8s/HAMi/Ingress preflight 수행
- `docker load`로 app/vLLM 이미지 적재
- vLLM 이미지와 `news_models/chandra-ocr-2` 모델 폴더 호환성 검사
- Harbor push
- k8s manifest apply
- 모델 PVC에 `chandra-ocr-2` 복사
- vLLM, OCR API, app rollout 대기
- app/OCR API/vLLM 내부 health 및 Ingress 외부 health 확인

preflight에서 막히는 대표 조건:

- `nocodeaidev` namespace 없음
- `nginx` IngressClass 없음
- `local-path` StorageClass 없음
- `harbor-reg-cred` Secret 없음
- `nocode-ai-army01` 노드 없음
- `nvidia.com/gpu` 잔여량이 1 미만
- 기존 Ingress가 `/a-cong-ocr` 또는 `/a-cong-ocr-api` prefix를 이미 사용 중

## 4. 수동 절차: 폐쇄망에서 Harbor에 이미지 넣기

```bash
cd /opt/defense-k8s-public-ocr-carry-in

docker load -i dist/a-cong-ocr_chandra.tar
docker load -i dist/a-cong-vllm-openai_chandra.tar

docker image inspect nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr:chandra >/dev/null
docker image inspect nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra >/dev/null

docker login nocodeaidev.army.mil:20443
docker push nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr:chandra
docker push nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra
```

Harbor 프로젝트명이 `nocodeaidev`가 아니면 매니페스트의 `image:` 값도 같은 경로로 바꿉니다.

## 5. 수동 절차: 매니페스트 수정

```bash
vi k8s/defense-remote-ocr.nocodeaidev.yaml
```

필수 확인:

- `image:` 3곳이 실제 Harbor 경로와 일치해야 합니다.
- `TARGET_API_BASE_URL`을 실제 callback 대상 `/news` 주소로 넣습니다.
- `TARGET_API_TOKEN`이 필요하면 Secret의 `TARGET_API_TOKEN` 값을 넣습니다.
- app `ROOT_PATH`는 `/a-cong-ocr`입니다.
- ocr-service `ROOT_PATH`는 `/a-cong-ocr-api`입니다.
- app Ingress 이름은 `a-cong-ocr-app`입니다.
- OCR API Ingress 이름은 `a-cong-ocr-api`입니다.
- vLLM은 Ingress가 없습니다.

## 6. 수동 절차: 배포

```bash
kubectl apply -f k8s/defense-remote-ocr.nocodeaidev.yaml
kubectl -n nocodeaidev get pvc
kubectl -n nocodeaidev get pods -l app.kubernetes.io/name=a-cong-ocr -o wide
```

PVC가 처음 만들어졌다면 모델 PVC가 비어 있습니다. 이 경우 6번을 진행합니다.

## 7. 수동 절차: 모델을 PVC에 넣기

PVC 안에서 `/models/chandra-ocr-2/config.json`으로 보여야 합니다.

```bash
cat >/tmp/a-cong-model-loader.yaml <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: a-cong-model-loader
  namespace: nocodeaidev
spec:
  restartPolicy: Never
  imagePullSecrets:
    - name: harbor-reg-cred
  containers:
    - name: loader
      image: nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr:chandra
      command: ["sleep", "86400"]
      volumeMounts:
        - name: models
          mountPath: /models
  volumes:
    - name: models
      persistentVolumeClaim:
        claimName: a-cong-ocr-models-pvc
EOF

kubectl apply -f /tmp/a-cong-model-loader.yaml
kubectl -n nocodeaidev wait --for=condition=Ready pod/a-cong-model-loader --timeout=180s
```

반입 폴더 안에 모델이 있으면:

```bash
kubectl -n nocodeaidev cp ./news_models/chandra-ocr-2 a-cong-model-loader:/models/chandra-ocr-2
```

서버에 이미 모델이 있으면:

```bash
kubectl -n nocodeaidev cp /data/news_models/chandra-ocr-2 a-cong-model-loader:/models/chandra-ocr-2
```

검증:

```bash
kubectl -n nocodeaidev exec a-cong-model-loader -- test -f /models/chandra-ocr-2/config.json
kubectl -n nocodeaidev exec a-cong-model-loader -- python3 - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path("/models/chandra-ocr-2/config.json").read_text())
print({"model_type": cfg.get("model_type"), "architectures": cfg.get("architectures")})
PY
kubectl -n nocodeaidev delete pod a-cong-model-loader
```

재시작:

```bash
kubectl -n nocodeaidev rollout restart deploy/a-cong-vllm-ocr
kubectl -n nocodeaidev rollout restart deploy/a-cong-ocr-service
kubectl -n nocodeaidev rollout restart deploy/a-cong-ocr-app
```

## 8. 상태 확인

```bash
chmod +x scripts/check_k8s_public_ocr.sh
scripts/check_k8s_public_ocr.sh
```

직접 확인하려면:

```bash
kubectl -n nocodeaidev get pods -l app.kubernetes.io/name=a-cong-ocr -o wide
kubectl -n nocodeaidev get svc a-cong-ocr-app a-cong-ocr-service a-cong-vllm-ocr
kubectl -n nocodeaidev get ingress a-cong-ocr-app a-cong-ocr-api
```

로그:

```bash
kubectl -n nocodeaidev logs deploy/a-cong-vllm-ocr --tail=200
kubectl -n nocodeaidev logs deploy/a-cong-ocr-service --tail=200
kubectl -n nocodeaidev logs deploy/a-cong-ocr-app --tail=200
```

외부 헬스체크:

```bash
curl -k https://nocodeaidev.army.mil:20443/a-cong-ocr/api/v1/health
curl -k https://nocodeaidev.army.mil:20443/a-cong-ocr-api/health
curl -k https://nocodeaidev.army.mil:20443/a-cong-ocr-api/api/v1/health
```

브라우저:

```text
https://nocodeaidev.army.mil:20443/a-cong-ocr/demo/jobs
```

## 9. OCR API 직접 호출

```bash
curl -k -X POST \
  https://nocodeaidev.army.mil:20443/a-cong-ocr-api/api/v1/ocr/image \
  -F "file=@./tmp_vllm_ocr_test.png" \
  -F "page_number=1"
```

정상 기대:

- HTTP 200
- JSON 응답
- `raw_vl.backend` 또는 관련 metadata가 `chandra`
- `model_id`가 `chandra-ocr-2`

## 10. 오류별 판단

`ImagePullBackOff`:

- Harbor image 경로가 틀렸거나 `harbor-reg-cred`가 해당 namespace에 없습니다.
- `kubectl -n nocodeaidev describe pod <pod>`의 Events를 봅니다.

`config.json not found`:

- 모델 PVC에 `/models/chandra-ocr-2/config.json`이 없습니다.
- 6번 모델 복사를 다시 수행합니다.

`transformers does not recognize this architecture`:

- 폐쇄망에서 새 패키지를 받을 수 없어서 발생합니다.
- 먼저 `kubectl -n nocodeaidev logs deploy/a-cong-vllm-ocr --tail=200`에서 `qwen_compat_mapping`을 봅니다.
- `qwen2_5`가 `true`로 나오고도 실패하면, 해당 현장 모델이 현재 이미지에 없는 custom code나 다른 architecture를 요구하는 것입니다.
- 현장 `pip install`로 해결하려 하지 말고, 인터넷 준비 PC에서 실제 현장 모델 폴더로 `scripts/validate_vllm_image_offline.sh`를 통과한 이미지를 다시 반입합니다.
- 절대 `pip install`을 폐쇄망 Pod 안에서 시도하지 않습니다.

`Pod Pending`, `Insufficient nvidia.com/gpu`:

- HAMi GPU 잔여량 부족입니다.
- `kubectl describe pod <vllm-pod>` Events를 봅니다.
- 기존 workload를 줄이거나 vLLM GPU 요청을 낮춥니다.

`CUDA out of memory`:

- `VLLM_GPU_MEMORY_UTILIZATION`을 `0.85`에서 `0.75`로 낮춥니다.
- HAMi `gpumem`이 Allocatable에 있으면 `nvidia.com/gpumem: "50000"` 근처부터 조정합니다.

`OCR API는 뜨는데 app 처리 실패`:

- app은 외부 OCR URL이 아니라 내부 `http://a-cong-ocr-service:8000`으로 호출해야 합니다.
- ConfigMap의 `OCR_SERVICE_URL`을 외부 Ingress URL로 바꾸지 마세요.

## 11. 롤백과 제거

롤백:

```bash
kubectl -n nocodeaidev rollout undo deploy/a-cong-ocr-app
kubectl -n nocodeaidev rollout undo deploy/a-cong-ocr-service
kubectl -n nocodeaidev rollout undo deploy/a-cong-vllm-ocr
```

제거:

```bash
kubectl delete -f k8s/defense-remote-ocr.nocodeaidev.yaml
```

PVC까지 삭제하면 모델과 처리 데이터가 삭제됩니다.

```bash
kubectl -n nocodeaidev delete pvc a-cong-ocr-models-pvc a-cong-ocr-model-cache-pvc a-cong-ocr-runtime-pvc
```
