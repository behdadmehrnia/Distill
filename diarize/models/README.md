# Offline pyannote weights for Distill diarization
#
# Required files (download with runtime/scripts/download_models.sh):
#   pyannote_diarization_config.yaml          (shipped)
#   pyannote_model_segmentation-3.0.bin
#   pyannote_model_wespeaker-voxceleb-resnet34-LM.bin
#
# Accept gated terms on Hugging Face for:
#   - pyannote/speaker-diarization-3.1
#   - pyannote/segmentation-3.0
#   - pyannote/wespeaker-voxceleb-resnet34-LM
#
# When these .bin files are present, the sidecar never needs Hub at runtime.
# Without them, Hub download is attempted only if HF_TOKEN validates with access.
# Otherwise set DIARIZATION_BACKEND=nemo (NVIDIA NeMo ClusteringDiarizer).
