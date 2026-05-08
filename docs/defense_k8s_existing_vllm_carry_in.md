# Existing vLLM K8s Carry-in Guide

이 번들은 현장 `nocodeaidev` namespace에 `a-cong-vllm-ocr`가 이미 정상 기동된 상태를 전제로 한다.
vLLM 이미지, 모델 PVC, 모델 캐시는 다시 반입하지 않는다.

## 이번 현장 오류 반영 사항

- `ImagePullBackOff/no basic auth credentials`: 기본값을 Harbor push/pull이 아니라 `docker load` + 로컬 이미지 + `imagePullPolicy: IfNotPresent`로 바꿨다.
- `Too many pods`: 기존 non-vLLM Pod를 먼저 0으로 내리고 정리한 뒤 `a-cong-ocr-service`, `a-cong-ocr-playground`, `a-cong-ocr-app` 순서로 하나씩 올린다.
- CPU/memory/Pod 슬롯 부족: 스크립트가 배포 전후로 노드 `allocatable`, 현재 Pod 수, OCR 3개 request, namespace ResourceQuota를 계산해 부족하면 원인을 출력하고 중단한다.
- 런타임/PVC 문제: Kubernetes runtime이 로컬 `docker load` 이미지를 볼 수 있는지 확인하고, 필수 PVC 3개가 `Bound`인지 먼저 검사한다.
- `Manifest missing app Ingress prefix`: preflight는 기본 비활성화하고, manifest 자체는 `/a-cong-ocr`, `/a-cong-ocr-api`, `/a-cong-ocr-playground`를 유지한다.
- `VLLM_EXPECT_MODEL_TYPE must be qwen3_5`: 기존 vLLM env 검사가 아니라 vLLM `/health`만 확인한다. vLLM Deployment는 적용/재시작 대상에서 제외한다.

## 반입 파일

- `dist/a-cong-ocr-app-ui_chandra.tar`
- `dist/a-cong-ocr-playground_chandra.tar`
- `dist/a-cong-ocr-api_chandra.tar`
- `k8s/defense-remote-ocr.nocodeaidev.yaml`
- `scripts/deploy_public_ocr_existing_vllm.sh`
- `scripts/check_existing_vllm_k8s.sh`
- `scripts/check_k8s_public_ocr.sh`
- `scripts/preflight_k8s_hami_public_ocr.sh`
- `SHA256SUMS.txt`
- `SHA256SUMS_FULL.txt`

## 실행

```bash
cd defense-k8s-existing-vllm-carry-in
chmod +x scripts/*.sh
./scripts/deploy_public_ocr_existing_vllm.sh
```

스크립트가 `PLAYGROUND_ADMIN_PASSWORD`를 물어보면 현장용 12자 이상 비밀번호를 입력한다.
기본 실행은 Harbor 인증을 요구하지 않는다. 이미지 tar를 로컬 Docker에 load하고 정확한 Harbor 형식 tag를 붙인 뒤 `IfNotPresent`로 실행한다.

전송 대상 API가 있으면 이렇게만 추가한다.

```bash
TARGET_API_BASE_URL='http://<내부대상>:<PORT>/news' \
TARGET_API_TOKEN='<필요시_토큰>' \
./scripts/deploy_public_ocr_existing_vllm.sh
```

## 스크립트가 하는 일

1. 기존 `a-cong-vllm-ocr` health를 확인한다.
2. Kubernetes runtime, 필수 PVC, manifest server dry-run, 노드/쿼터 용량을 사전 검사한다.
3. app/playground/OCR API tar 3개를 `docker load`한다.
4. 로컬 이미지에 다음 tag가 있는지 보장한다.
   - `nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-app-ui:chandra`
   - `nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-playground:chandra`
   - `nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-api:chandra`
5. 기존 `a-cong-ocr-service`, `a-cong-ocr-playground`, `a-cong-ocr-app`만 0으로 내리고 남은 Pod를 정리한다.
6. OCR 3개가 최종적으로 들어갈 수 있는지 Pod 슬롯, CPU/memory request, ResourceQuota를 다시 시뮬레이션한다.
7. vLLM Deployment와 PVC는 건드리지 않고 manifest를 적용한다.
8. non-vLLM 3개 Deployment를 service, playground, app 순서로 하나씩 `replicas=1`로 올린다.
9. 내부 health와 Ingress health를 확인한다.

## 정상 목표

```bash
kubectl -n nocodeaidev get pods -l app.kubernetes.io/name=a-cong-ocr -o wide
```

정상 목표:

- `a-cong-vllm-ocr`: `Running`
- `a-cong-ocr-service`: `Running`
- `a-cong-ocr-playground`: `Running`
- `a-cong-ocr-app`: `Running`

## 확인 URL

- App: `https://nocodeaidev.army.mil:20443/a-cong-ocr/demo/jobs`
- App health: `https://nocodeaidev.army.mil:20443/a-cong-ocr/api/v1/health`
- OCR API health: `https://nocodeaidev.army.mil:20443/a-cong-ocr-api/health`
- OCR API image: `https://nocodeaidev.army.mil:20443/a-cong-ocr-api/api/v1/ocr/image`
- Playground: `https://nocodeaidev.army.mil:20443/a-cong-ocr-playground/`

## 옵션

- Harbor에도 push해야 하면 `SKIP_HARBOR_PUSH=0 ./scripts/deploy_public_ocr_existing_vllm.sh`
- 외부 Ingress health를 나중에 따로 확인하려면 `RUN_EXTERNAL_CHECKS=0 ./scripts/deploy_public_ocr_existing_vllm.sh`
- 용량 시뮬레이션을 끄려면 `RUN_CAPACITY_CHECK=0 ./scripts/deploy_public_ocr_existing_vllm.sh`
- Docker runtime 강제 검사를 끄려면 `REQUIRE_DOCKER_RUNTIME=0 ./scripts/deploy_public_ocr_existing_vllm.sh`
- preflight를 다시 켜려면 `SKIP_PREFLIGHT=0 ./scripts/deploy_public_ocr_existing_vllm.sh`

UI/API 수정에서는 `a-cong-vllm-ocr`를 재시작하지 않는다.
