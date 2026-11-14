# Сборка Windows EXE без Python на компьютере

1. Создай новый приватный репозиторий на https://github.com/new (например, `GamePilotFishing`).
2. Распакуй ZIP и загрузи **содержимое** архива в **корень** репозитория, не добавляя лишнюю внешнюю папку. В частности, путь должен быть `.github/workflows/build-windows.yml`.
3. GitHub Actions автоматически начнёт тесты и сборку на коммите в `main` или запусти **Actions → Build GamePilot Fishing Windows EXE → Run workflow**.
4. Если сборка успешна, открой запуск → **Artifacts → GamePilotFishing-Windows**.
5. Распакуй ZIP, внутри будет `GamePilotFishing.exe`.

**Важно:** успешная сборка означает, что EXE собран и тесты прошли. Не означает, что программа автоматически ловит рыбу на твоём клиенте без калибровки.
