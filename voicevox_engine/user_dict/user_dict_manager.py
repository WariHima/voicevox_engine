"""ユーザー辞書関連の処理"""

import json
import sys
import threading
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, TypeVar
from uuid import UUID, uuid4

import pyopenjtalk
from pydantic import TypeAdapter

from ..utility.path_utility import get_save_dir, resource_root
from .model import UserDictWord
from .user_dict_word import (
    SaveFormatUserDictWord,
    UserDictInputError,
    WordProperty,
    convert_from_save_format,
    convert_to_save_format,
    create_word,
    part_of_speech_data,
    priority2cost,
)

F = TypeVar("F", bound=Callable[..., Any])


def _mutex_wrapper(lock: threading.Lock) -> Callable[[F], F]:
    def wrap(f: F) -> F:
        def func(*args: Any, **kw: Any) -> Any:
            lock.acquire()
            try:
                return f(*args, **kw)
            finally:
                lock.release()

        return func  # type: ignore

    return wrap


resource_dir = resource_root()
save_dir = get_save_dir()

if not save_dir.is_dir():
    save_dir.mkdir(parents=True)

# デフォルトのファイルパス
DEFAULT_DICT_PATH: Final = resource_dir / "default.csv"  # VOICEVOXデフォルト辞書
_USER_DICT_PATH: Final = save_dir / "user_dict.json"  # ユーザー辞書


# 同時書き込みの制御
mutex_user_dict = threading.Lock()
mutex_openjtalk_dict = threading.Lock()


_save_format_dict_adapter = TypeAdapter(dict[str, SaveFormatUserDictWord])


def _delete_file_on_close(file_path: Path) -> None:
    """
    ファイルのハンドルが全て閉じたときにファイルを削除する。OpenJTalk用のカスタム辞書用。

    WindowsではCreateFileW関数で`FILE_FLAG_DELETE_ON_CLOSE`を付けてすぐに閉じることで、
    `FILE_SHARE_DELETE`を付けて開かれているファイルのハンドルが全て閉じた時に削除されるようにする。

    Windows以外では即座にファイルを削除する。
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes.wintypes import DWORD, HANDLE, LPCWSTR

        _CreateFileW = ctypes.windll.kernel32.CreateFileW
        _CreateFileW.argtypes = [
            LPCWSTR,
            DWORD,
            DWORD,
            ctypes.c_void_p,
            DWORD,
            DWORD,
            HANDLE,
        ]
        _CreateFileW.restype = HANDLE
        _CloseHandle = ctypes.windll.kernel32.CloseHandle
        _CloseHandle.argtypes = [HANDLE]

        _FILE_SHARE_DELETE = 0x00000004
        _FILE_SHARE_READ = 0x00000001
        _OPEN_EXISTING = 3
        _FILE_FLAG_DELETE_ON_CLOSE = 0x04000000
        _INVALID_HANDLE_VALUE = HANDLE(-1).value

        h_file = _CreateFileW(
            str(file_path),
            0,
            _FILE_SHARE_DELETE | _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_DELETE_ON_CLOSE,
            None,
        )
        if h_file == _INVALID_HANDLE_VALUE:
            raise RuntimeError(
                f"Failed to CreateFileW for {file_path}"
            ) from ctypes.WinError()

        result = _CloseHandle(h_file)
        if result == 0:
            raise RuntimeError(
                f"Failed to CloseHandle for {file_path}"
            ) from ctypes.WinError()
    else:
        file_path.unlink()


class UserDictionary:
    """ユーザー辞書"""

    # FIXME: OpenJTalk 用辞書の管理機能を備えているため、このクラスは Manager としても捉えられる。クラス名変更や役割分割等を検討。
    def __init__(
        self,
        default_dict_path: Path = DEFAULT_DICT_PATH,
        user_dict_path: Path = _USER_DICT_PATH,
    ) -> None:
        """
        ユーザー辞書を利用可能にする。

        Parameters
        ----------
        default_dict_path : Path
            デフォルト辞書ファイルのパス
        user_dict_path : Path
            ユーザー辞書ファイルのパス
        """
        self._default_dict_path = default_dict_path
        self._user_dict_path = user_dict_path
        self.update_dict()

    @_mutex_wrapper(mutex_user_dict)
    def _write_to_json(self, user_dict: dict[str, UserDictWord]) -> None:
        """ユーザー辞書データをファイルへ書き込む。"""
        save_format_user_dict: dict[str, SaveFormatUserDictWord] = {}
        for word_uuid, word in user_dict.items():
            save_format_word = convert_to_save_format(word)
            save_format_user_dict[word_uuid] = save_format_word
        user_dict_json = _save_format_dict_adapter.dump_json(save_format_user_dict)
        self._user_dict_path.write_bytes(user_dict_json)

    @_mutex_wrapper(mutex_openjtalk_dict)
    def update_dict(self) -> None:
        """辞書を更新する。"""
        default_dict_path = self._default_dict_path
        user_dict_path = self._user_dict_path

        random_string = uuid4()
        tmp_csv_path = user_dict_path.with_name(
            f"user.dict_csv-{random_string}.tmp"
        )  # csv形式辞書データの一時保存ファイル
        tmp_compiled_path = user_dict_path.with_name(
            f"user.dict_compiled-{random_string}.tmp"
        )  # コンパイル済み辞書データの一時保存ファイル

        try:
            # 辞書.csvを作成
            csv_text = ""

            # デフォルト辞書データの追加
            if not default_dict_path.is_file():
                warnings.warn("Cannot find default dictionary.", stacklevel=1)
                return
            default_dict = default_dict_path.read_text(encoding="utf-8")
            if default_dict == default_dict.rstrip():
                default_dict += "\n"
            csv_text += default_dict

            # ユーザー辞書データの追加
            user_dict = self.read_dict()
            for word_uuid in user_dict:
                word = user_dict[word_uuid]
                csv_text += (
                    "{surface},{context_id},{context_id},{cost},{part_of_speech},"
                    + "{part_of_speech_detail_1},{part_of_speech_detail_2},"
                    + "{part_of_speech_detail_3},{inflectional_type},"
                    + "{inflectional_form},{stem},{yomi},{pronunciation},"
                    + "{accent_type}/{mora_count},{accent_associative_rule}\n"
                ).format(
                    surface=word.surface,
                    context_id=word.context_id,
                    cost=priority2cost(word.context_id, word.priority),
                    part_of_speech=word.part_of_speech,
                    part_of_speech_detail_1=word.part_of_speech_detail_1,
                    part_of_speech_detail_2=word.part_of_speech_detail_2,
                    part_of_speech_detail_3=word.part_of_speech_detail_3,
                    inflectional_type=word.inflectional_type,
                    inflectional_form=word.inflectional_form,
                    stem=word.stem,
                    yomi=word.yomi,
                    pronunciation=word.pronunciation,
                    accent_type=word.accent_type,
                    mora_count=word.mora_count,
                    accent_associative_rule=word.accent_associative_rule,
                )
            # 辞書データを辞書.csv へ一時保存
            tmp_csv_path.write_text(csv_text, encoding="utf-8")

            # 辞書.csvをOpenJTalk用にコンパイル
            pyopenjtalk.mecab_dict_index(str(tmp_csv_path), str(tmp_compiled_path))
            if not tmp_compiled_path.is_file():
                raise RuntimeError("辞書のコンパイル時にエラーが発生しました。")

            # コンパイル済み辞書の読み込み
            pyopenjtalk.update_global_jtalk_with_user_dict(
                str(tmp_compiled_path.resolve(strict=True))
            )  # NOTE: resolveによりコンパイル実行時でも相対パスを正しく認識できる

        except Exception as e:
            raise e

        finally:
            # 後処理
            if tmp_csv_path.exists():
                tmp_csv_path.unlink()
            if tmp_compiled_path.exists():
                _delete_file_on_close(tmp_compiled_path)

    @_mutex_wrapper(mutex_user_dict)
    def read_dict(self) -> dict[str, UserDictWord]:
        """ユーザー辞書を読み出す。"""
        # 指定ユーザー辞書が存在しない場合、空辞書を返す
        if not self._user_dict_path.is_file():
            return {}

        with self._user_dict_path.open(encoding="utf-8") as f:
            save_format_dict = _save_format_dict_adapter.validate_python(json.load(f))
            result: dict[str, UserDictWord] = {}
            for word_uuid, word in save_format_dict.items():
                result[str(UUID(word_uuid))] = convert_from_save_format(word)
        return result

    def import_user_dict(
        self, dict_data: dict[str, UserDictWord], override: bool = False
    ) -> None:
        """
        ユーザー辞書をインポートする。

        Parameters
        ----------
        dict_data : dict[str, UserDictWord]
            インポートするユーザー辞書のデータ
        override : bool
            重複したエントリがあった場合、上書きするかどうか
        """
        # インポートする辞書データのバリデーション
        for word_uuid, word in dict_data.items():
            UUID(word_uuid)
            for pos_detail in part_of_speech_data.values():
                if word.context_id == pos_detail.context_id:
                    assert word.part_of_speech == pos_detail.part_of_speech
                    assert (
                        word.part_of_speech_detail_1
                        == pos_detail.part_of_speech_detail_1
                    )
                    assert (
                        word.part_of_speech_detail_2
                        == pos_detail.part_of_speech_detail_2
                    )
                    assert (
                        word.part_of_speech_detail_3
                        == pos_detail.part_of_speech_detail_3
                    )
                    assert (
                        word.accent_associative_rule
                        in pos_detail.accent_associative_rules
                    )
                    break
            else:
                raise ValueError("対応していない品詞です")

        # 既存辞書の読み出し
        old_dict = self.read_dict()

        # 辞書データの更新
        # 重複エントリの上書き
        if override:
            new_dict = {**old_dict, **dict_data}
        # 重複エントリの保持
        else:
            new_dict = {**dict_data, **old_dict}

        # 更新された辞書データの保存と適用
        self._write_to_json(new_dict)
        self.update_dict()

    def apply_word(self, word_property: WordProperty) -> str:
        """新規単語を追加し、その単語に割り当てられた UUID を返す。"""
        # 新規単語の追加による辞書データの更新
        user_dict = self.read_dict()
        word_uuid = str(uuid4())
        user_dict[word_uuid] = create_word(word_property)

        # 更新された辞書データの保存と適用
        self._write_to_json(user_dict)
        self.update_dict()

        return word_uuid

    def rewrite_word(self, word_uuid: str, word_property: WordProperty) -> None:
        """単語 UUID で指定された単語を上書き更新する。"""
        # 既存単語の上書きによる辞書データの更新
        user_dict = self.read_dict()
        if word_uuid not in user_dict:
            raise UserDictInputError("UUIDに該当するワードが見つかりませんでした")
        user_dict[word_uuid] = create_word(word_property)

        # 更新された辞書データの保存と適用
        self._write_to_json(user_dict)
        self.update_dict()

    def delete_word(self, word_uuid: str) -> None:
        """単語UUIDで指定された単語を削除する。"""
        # 既存単語の削除による辞書データの更新
        user_dict = self.read_dict()
        if word_uuid not in user_dict:
            raise UserDictInputError("IDに該当するワードが見つかりませんでした")
        del user_dict[word_uuid]

        # 更新された辞書データの保存と適用
        self._write_to_json(user_dict)
        self.update_dict()
