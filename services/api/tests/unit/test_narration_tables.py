"""Unit tests: semantic table narration + code/log summarization (VOICE_SPEC §6)."""

from app.narration.tables import narrate_code_or_log, narrate_table, parse_markdown_table

TABLE = """| Bileşen | Durum | Gecikme |
|---|---|---:|
| API | Sağlıklı | 82 ms |
| Voice | Sağlıklı | 240 ms |
| Browser Agent | Uyarı | 1,8 sn |"""


def test_parse_markdown_table() -> None:
    model = parse_markdown_table(TABLE)
    assert model is not None
    assert model.headers == ["Bileşen", "Durum", "Gecikme"]
    assert len(model.rows) == 3
    assert model.status_col == 1  # "Durum"


def test_semantic_narration_leads_with_the_warning_row() -> None:
    result = narrate_table(TABLE)
    assert result.row_count == 3
    assert result.highlighted == ["Browser Agent"]
    # semantic: mentions the notable finding + its metric, not a literal dump
    assert "Browser Agent" in result.text
    assert "uyarı durumunda" in result.text
    assert "bir virgül sekiz" in result.text  # 1,8 normalized
    # the healthy rows' latency numbers are NOT dumped in semantic mode
    assert "seksen iki" not in result.text


def test_literal_mode_dumps_every_cell() -> None:
    result = narrate_table(TABLE, literal=True)
    assert "seksen iki ms" in result.text  # 82 ms literal
    assert "iki yüz kırk ms" in result.text  # 240 ms literal
    assert "Browser Agent" in result.text


def test_all_healthy_table() -> None:
    table = """| Servis | Durum |
|---|---|
| API | Sağlıklı |
| Voice | Sağlıklı |"""
    result = narrate_table(table)
    assert "Tüm bileşenler sağlıklı" in result.text
    assert not result.highlighted


def test_error_row_uses_error_phrasing() -> None:
    table = """| Servis | Durum |
|---|---|
| Ödeme | Hata |
| Kimlik | Sağlıklı |"""
    result = narrate_table(table)
    assert "Ödeme" in result.highlighted
    assert "hata durumunda" in result.text


def test_non_table_input_falls_back_to_normalizer() -> None:
    result = narrate_table("Sadece 42 kelime.")
    assert result.row_count == 0
    assert "kırk iki" in result.text


def test_code_or_log_summary_flags_errors() -> None:
    log = "INFO starting\nINFO connected\nERROR timeout after 30s\nINFO retry"
    out = narrate_code_or_log(log)
    assert "hata veya uyarı" in out
    assert "ERROR timeout" in out


def test_code_or_log_summary_clean() -> None:
    out = narrate_code_or_log("INFO ok\nINFO done")
    assert "kritik hata veya uyarı bulunmuyor" in out


def test_code_or_log_literal() -> None:
    block = "line one\nline two"
    assert narrate_code_or_log(block, literal=True) == block
