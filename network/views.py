import csv
import io
import json
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt


def index(request):
    return render(request, "index.html")


def redactor(request):
    path = Path(settings.BASE_DIR) / "static" / "topology.json"
    try:
        with open(path) as json_file:
            init_data = json.load(json_file)
    except FileNotFoundError:
        # Если файл не найден, создаем пустую структуру
        init_data = {"nodes": [], "links": []}
    except json.JSONDecodeError:
        # Если файл поврежден, создаем пустую структуру
        init_data = {"nodes": [], "links": []}
    except Exception as e:
        # Логируем ошибку
        print(f"Ошибка при загрузке файла: {e}")
        init_data = {"nodes": [], "links": []}

    # Передаем данные в шаблон
    context = {
        'init_data': json.dumps(init_data),
    }
    return render(request, "redactor.html", context)


def upload_csv(request):
    if request.method == "POST" and request.FILES.get("file"):
        file = request.FILES["file"]
        file_ext = os.path.splitext(file.name)[1].lower()

        # Обработка Excel файлов
        if file_ext in ['.xls', '.xlsx']:
            return process_excel(file)
        # Обработка CSV файлов
        elif file_ext == '.csv':
            return process_csv(file)
        else:
            return JsonResponse({"error": "Неподдерживаемый формат файла. Используйте CSV, XLS или XLSX."}, status=400)

    return JsonResponse({"error": "Неверный запрос"}, status=400)


def process_excel(file):
    """Обработка Excel файлов с преобразованием в CSV"""
    try:
        # Загружаем файл Excel в pandas
        df = pd.read_excel(file, engine='openpyxl')

        # Удаляем строки, где все значения NaN
        df = df.dropna(how='all')

        # Заполняем пропущенные значения в ключевых столбцах (объединенные ячейки)
        for col in df.columns:
            col_name = str(col).lower()
            if any(keyword in col_name for keyword in ['секция', 'dwdm', 'маршрут']):
                df[col] = df[col].ffill()

        # Создаем временный буфер для CSV
        csv_buffer = io.StringIO()

        # Записываем DataFrame в CSV
        df.to_csv(csv_buffer, sep=';', index=False)

        # Перемещаем указатель в начало буфера
        csv_buffer.seek(0)

        # Используем существующую функцию process_csv для обработки полученного CSV
        # Обернем StringIO в BytesIO, чтобы имитировать файловый объект с методом read()
        byte_buffer = io.BytesIO(csv_buffer.getvalue().encode('utf-8'))
        byte_buffer.name = "converted_excel.csv"  # Для имитации атрибута name файла

        # Передаем преобразованный файл в функцию process_csv
        return process_csv(byte_buffer)

    except Exception as e:
        import traceback
        traceback_str = traceback.format_exc()
        print(f"Ошибка при преобразовании Excel в CSV: {str(e)}\n{traceback_str}")
        return JsonResponse({"error": f"Ошибка при обработке Excel: {str(e)}"}, status=400)


def process_csv(file):
    """Обработка CSV файлов"""
    try:
        # Если входной файл - это BytesIO
        if isinstance(file, io.BytesIO):
            decoded_file = file.read().decode("utf-8")
        else:
            # Если это обычный загруженный файл
            decoded_file = file.read().decode("utf-8")

        io_string = io.StringIO(decoded_file)
        reader = csv.DictReader(io_string, delimiter=';')

        # Получаем заголовки
        fieldnames = reader.fieldnames
        print("Заголовки CSV:", fieldnames)

        # Найдем нужные столбцы по частичным совпадениям
        route_column = None
        capacity_column = None
        usage_column = None
        distance_column = None

        for field in fieldnames:
            field_lower = str(field).lower()
            if not route_column and any(keyword in field_lower for keyword in ['секция', 'dwdm', 'маршрут']):
                route_column = field
            elif not capacity_column and any(keyword in field_lower for keyword in ['частот', 'ресурс', 'емкост']):
                capacity_column = field
            elif not usage_column and any(keyword in field_lower for keyword in ['загруз', 'использ', 'занято']):
                usage_column = field
            elif not distance_column and any(keyword in field_lower for keyword in ['протяж', 'расстоян', 'км']):
                distance_column = field

        print(
            f"Найденные столбцы: маршрут={route_column}, емкость={capacity_column}, загрузка={usage_column}, расстояние={distance_column}")

        if not all([route_column, capacity_column, usage_column]):
            missing = []
            if not route_column: missing.append("маршрут")
            if not capacity_column: missing.append("емкость")
            if not usage_column: missing.append("загрузка")
            return JsonResponse({"error": f"Не найдены необходимые столбцы: {', '.join(missing)}"}, status=400)

        # Возвращаем указатель в начало файла и снова читаем
        io_string.seek(0)
        reader = csv.DictReader(io_string, delimiter=';')

        nodes = set()
        edges = []

        for row in reader:
            # Убедимся, что нужные столбцы существуют
            if route_column not in row or capacity_column not in row or usage_column not in row:
                continue

            section = row.get(route_column, "").strip()

            # Пропускаем строки с заголовками и пустыми значениями
            if not section or section.lower() == 'секция dwdm' or section.lower() == 'всего':
                continue

            # Проверяем разные типы разделителей
            separators = ['-', '—', '–', '→', '>']
            found_separator = None

            for sep in separators:
                if sep in section:
                    found_separator = sep
                    break

            if not found_separator:
                continue

            parts = section.split(found_separator)
            if len(parts) != 2:
                continue

            node_a, node_b = parts[0].strip(), parts[1].strip()

            # Обезличивание городов
            node_a = obfuscate_city_name(node_a)
            node_b = obfuscate_city_name(node_b)

            nodes.update([node_a, node_b])

            # Безопасное получение числовых значений
            try:
                total_str = row.get(capacity_column, "0").strip()
                total = float(total_str) if total_str and total_str != 'nan' else 0
            except (ValueError, TypeError):
                total = 0

            try:
                usage_str = row.get(usage_column, "0").strip()
                # Проверка на процентный формат
                if '%' in usage_str:
                    used_percent = float(usage_str.replace('%', '').strip())
                    used = used_percent / 100 * total
                else:
                    used = float(usage_str) if usage_str and usage_str != 'nan' else 0
            except (ValueError, TypeError):
                used = 0

            try:
                distance_str = row.get(distance_column, "0").strip() if distance_column else "0"
                distance = float(distance_str) if distance_str and distance_str != 'nan' else 0
            except (ValueError, TypeError):
                distance = 0

            # Расчет соотношения использования
            ratio = min(1.0, used / total) if total > 0 else 0

            # Определяем цвет линии на основе загрузки
            color = "green" if ratio < 0.5 else "yellow" if ratio < 0.75 else "red"

            edges.append({
                "from": node_a,
                "to": node_b,
                "label": f"{int(used)}/{int(total)}",
                "title": f"Расстояние: {distance} км\nЗагрузка: {ratio:.1%}",
                "distance": distance,
                "color": {"color": color},
                "width": 2 + ratio * 5,
                "usage_ratio": ratio
            })

        if not edges:
            return JsonResponse({"error": "Не удалось извлечь данные о соединениях. Проверьте формат файла."},
                                status=400)

        return JsonResponse({
            "nodes": [{"id": node, "label": node} for node in nodes],
            "edges": edges
        })

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Ошибка при обработке CSV: {str(e)}\n{error_trace}")
        return JsonResponse({"error": f"Ошибка при обработке CSV: {str(e)}"}, status=400)


def obfuscate_city_name(city_name):
    """Обезличивание названий городов"""
    # Создаем хэш имени, чтобы обеспечить консистентность замены
    import hashlib
    hash_obj = hashlib.md5(city_name.encode())
    hash_hex = hash_obj.hexdigest()

    # Используем первые 8 символов хэша как уникальный идентификатор
    # Префикс "City_" делает понятным, что это город
    return f"City_{hash_hex[:8]}"


def generate_mock_data(request):
    path = Path(settings.BASE_DIR) / "static" / "topology.json"
    with open(path, encoding="utf-8") as f:
        topology = json.load(f)

    total = 120
    edges = []

    for frm, to in topology["links"]:
        used = round(random.uniform(0, total), 1)
        distance = round(random.uniform(50, 500), 1)  # Случайное расстояние
        ratio = used / total
        color = "green" if ratio < 0.5 else "yellow" if ratio < 0.75 else "red"

        # Создаём рёбра с дополнительными параметрами для отображения
        edges.append({
            "from": frm,
            "to": to,
            "label": f"{int(used)}/{int(total)}",
            "title": f"Расстояние: {distance} км\nЗагрузка: {ratio:.1%}",
            "distance": distance,
            "color": {"color": color},
            "width": 2 + ratio * 5,
            "usage_ratio": ratio
        })

    return JsonResponse({
        "nodes": topology["nodes"],
        "edges": edges
    })


@csrf_exempt
def save_topology(request):
    if request.method == "POST":
        data = json.loads(request.body)
        nodes = data.get("nodes", [])
        edges = data.get("links", [])

        # Сохраняем данные как есть, без изменений
        topology = {
            "nodes": nodes,
            "links": edges  # Оставляем рёбра как есть
        }

        # Сохраняем в файл
        path = Path(settings.BASE_DIR) / "static" / "topology.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(topology, f, ensure_ascii=False, indent=4)

        return JsonResponse({"status": "success"})

    return JsonResponse({"error": "Неверный запрос"}, status=400)
