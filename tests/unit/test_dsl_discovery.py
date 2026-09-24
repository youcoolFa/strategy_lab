"""dsl/discovery.py:掃描 strategies/*.yaml + 互動式選單,demo/run_from_yaml.py
跟 live/select_strategy.py 共用同一套邏輯,不各寫一份。用假的
input_fn/print_fn 注入來測試,不用真的等鍵盤輸入(呼應這個 repo 其他地方
now_fn/http_client 那種依賴注入的可測試性做法)。"""

import pytest

from strategy_lab.dsl.discovery import list_strategy_files, prompt_strategy_choice


class TestListStrategyFiles:
    def test_returns_yaml_files_sorted_by_name(self, tmp_path):
        (tmp_path / "b_strategy.yaml").write_text("")
        (tmp_path / "a_strategy.yaml").write_text("")
        (tmp_path / "not_yaml.txt").write_text("")

        result = list_strategy_files(tmp_path)

        assert result == [tmp_path / "a_strategy.yaml", tmp_path / "b_strategy.yaml"]

    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert list_strategy_files(tmp_path) == []


class TestPromptStrategyChoice:
    def _files(self, tmp_path):
        a = tmp_path / "weekend_mean_reversion.yaml"
        b = tmp_path / "ma_crossover_bracket.yaml"
        a.write_text("")
        b.write_text("")
        return [a, b]

    def test_valid_choice_returns_matching_file(self, tmp_path):
        files = self._files(tmp_path)
        inputs = iter(["2"])
        result = prompt_strategy_choice(files, input_fn=lambda _: next(inputs), print_fn=lambda _: None)
        assert result == files[1]

    def test_non_numeric_input_reprompts_until_valid(self, tmp_path):
        files = self._files(tmp_path)
        inputs = iter(["abc", "1"])
        result = prompt_strategy_choice(files, input_fn=lambda _: next(inputs), print_fn=lambda _: None)
        assert result == files[0]

    def test_out_of_range_input_reprompts_until_valid(self, tmp_path):
        files = self._files(tmp_path)
        inputs = iter(["0", "99", "2"])
        result = prompt_strategy_choice(files, input_fn=lambda _: next(inputs), print_fn=lambda _: None)
        assert result == files[1]

    def test_menu_is_printed_with_one_based_numbering(self, tmp_path):
        files = self._files(tmp_path)
        printed = []
        prompt_strategy_choice(files, input_fn=lambda _: "1", print_fn=printed.append)
        assert "  1. weekend_mean_reversion" in printed
        assert "  2. ma_crossover_bracket" in printed

    def test_empty_file_list_raises_value_error(self):
        with pytest.raises(ValueError):
            prompt_strategy_choice([])
