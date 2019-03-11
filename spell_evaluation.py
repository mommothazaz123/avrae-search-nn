import json
import sys
import time

import numpy as np
import tensorflow as tf
from fuzzywuzzy import fuzz, process
from tabulate import tabulate

from preprocess import MAGIC_1, MAGIC_2, clean, tokenize


def load_model(name):
    model = tf.keras.models.load_model(f'models/{name}.h5')
    return model


def load_map():
    """
    Map: index -> spell name
    """
    with open('preprocessing/map-mar2019_861k_spell.json') as f:
        map_ = json.load(f)
    map_ = {int(k): v for k, v in map_.items()}
    reverse_map = {v: k for k, v in map_.items()}
    return map_, reverse_map


def load_choices():
    with open('res/spell.json') as f:
        data = json.load(f)
    return data


def load_evaluation_queries():
    with open('preprocessing/evaluation-mar2019_861k_spell.json') as f:
        data = json.load(f)
    data = [(e['query'], e['result']) for e in data]
    return data


def naive_partial_match(choices, query, return_weights=False):
    """Returns the names of the top 5 results using this search algorithm."""
    full_matches = [s['name'] for s in choices if s['name'].lower() == query.lower()]
    partial_matches = [s['name'] for s in choices if
                       query.lower() in s['name'].lower() and s['name'] not in full_matches]
    results = full_matches + partial_matches
    if not return_weights:
        return results
    weights = [len(query) / len(r) for r in results]
    weighted_results = sorted(list(zip(results, weights)), key=lambda e: e[1], reverse=True)
    return weighted_results


def naive_levenshtein_distance(choices, query):
    names = [s['name'] for s in choices]
    fuzzy_results = process.extract(query, names, scorer=fuzz.ratio)
    sorted_weighted = sorted(fuzzy_results, key=lambda e: e[1], reverse=True)

    # build results list, unique
    results = []
    for r in sorted_weighted:
        if r[0] not in results:
            results.append(r[0])

    return results


def pure_model(choices, query, model, magic_string, model_name, return_weights=False):
    query = clean(query)
    query = tokenize(query, magic_string)
    query = np.expand_dims(query, 0)
    if 'conv' in model_name and 'embedding' not in model_name:
        query = np.expand_dims(query, 2)

    prediction = model.predict(query)
    prediction = prediction[0]

    indexed = list(enumerate(prediction))
    weighted = sorted(indexed, key=lambda e: e[1], reverse=True)
    if not return_weights:
        return [choices[r[0]]['name'] for r in weighted[:10]]
    return [(choices[r[0]]['name'], r[1]) for r in weighted[:10]]


def mixed_model(choices, query, model, magic_string, model_name, return_weights=False):
    names = [s['name'] for s in choices]
    fuzzy_results = process.extract(query, names, scorer=fuzz.ratio)
    fuzzy_sum = max(sum(r[1] for r in fuzzy_results), 0.001)
    fuzzy_matches_and_confidences = [(r[0], r[1] / fuzzy_sum) for r in fuzzy_results]

    # net
    query = clean(query)
    query = tokenize(query, magic_string)
    query = np.expand_dims(query, 0)
    if 'conv' in model_name:
        query = np.expand_dims(query, 2)

    prediction = model.predict(query)
    prediction = prediction[0]

    indexed = list(enumerate(prediction))
    weighted = sorted(indexed, key=lambda e: e[1], reverse=True)
    net_weighted = [(choices[r[0]]['name'], r[1]) for r in weighted]

    sorted_weighted = sorted(fuzzy_matches_and_confidences + net_weighted, key=lambda e: e[1], reverse=True)

    # build results list, unique
    results = []
    weights = []
    for r in sorted_weighted:
        if r[0] not in results:
            results.append(r[0])
            weights.append(r[1])
    if not return_weights:
        return results
    return list(zip(results, weights))


def evaluate(search, query_pairs, choices, model=None, reverse_map=None, magic_string=None, model_name=None):
    start = time.time()
    top_1 = 0
    top_2 = 0
    top_3 = 0
    top_10 = 0
    fail = 0

    for query, expected_result in query_pairs:
        expected_result_name = reverse_map[expected_result]
        if model is None:
            top_5 = search(choices, query)
        else:
            top_5 = search(choices, query, model, magic_string, model_name)

        if len(top_5) > 0 and top_5[0] == expected_result_name:
            top_1 += 1
        elif len(top_5) > 1 and top_5[1] == expected_result_name:
            top_2 += 1
        elif len(top_5) > 2 and top_5[2] == expected_result_name:
            top_3 += 1
        elif len(top_5) > 2 and expected_result_name in top_5[:10]:
            top_10 += 1
        else:
            fail += 1
    end = time.time()

    return top_1, top_2, top_3, fail, end - start, top_10


def interactive_search(choices, models, map_, last_model, last_model_name):
    if not len(models):
        print("At least 1 model must be evaluated for interactive search")
        return
    while True:
        query = input("Query? ")
        top_naive_partial = naive_partial_match(choices, query, return_weights=True)[:5]
        top_models = [
            (model_name, pure_model(choices, query, model,
                                    MAGIC_1 if model_name.startswith('magic1') else MAGIC_2, model_name,
                                    return_weights=True)[:5])
            for model_name, model in models.items()
        ]
        top_mixed = mixed_model(choices, query, last_model,
                                MAGIC_1 if last_model_name.startswith('magic1') else MAGIC_2, last_model_name,
                                return_weights=True)[:5]

        # print(top_naive_partial)
        # print(top_models)
        # print(top_mixed)

        headers = ['baseline'] + [m[0] for m in top_models] + ['mixed']
        rows = []
        for i in range(5):
            if i < len(top_naive_partial):
                naive_partial = f"{top_naive_partial[i][1]:>5.1%}: {top_naive_partial[i][0]}"
            else:
                naive_partial = "    ?: ?"
            row = [naive_partial] \
                  + [f"{m[1][i][1]:>5.1%}: {m[1][i][0]}" for m in top_models] \
                  + [f"{top_mixed[i][1]:>5.1%}: {top_mixed[i][0]}"]
            rows.append(row)

        print(tabulate(rows, headers=headers))


if __name__ == '__main__':
    models = {}
    last_model = None
    last_model_name = None
    num_models = int(input("Num models to evaluate? "))
    for _ in range(num_models):
        model_name = input("Model name? ").strip()
        model = load_model(model_name)
        models[model_name] = model
        last_model = model
        last_model_name = model_name

    map_, reverse_map = load_map()
    choices = load_choices()
    query_pairs = load_evaluation_queries()

    if 'interactive' in sys.argv:
        interactive_search(choices, models, map_, last_model, last_model_name)
    else:
        t1, t2, t3, f, t, t10 = evaluate(naive_partial_match, query_pairs, choices, reverse_map=map_)
        print(f"Naive Partial Match: t1={t1} t2={t2} t3={t3} t10={t10} f={f} t={t:.2f}")
        t1, t2, t3, f, t, t10 = evaluate(naive_levenshtein_distance, query_pairs, choices, reverse_map=map_)
        print(f"Naive Levenshtein: t1={t1} t2={t2} t3={t3} t10={t10} f={f} t={t:.2f}")
        for model_name, model in models.items():
            t1, t2, t3, f, t, t10 = evaluate(pure_model, query_pairs, choices, model=model, reverse_map=map_,
                                             model_name=model_name,
                                             magic_string=MAGIC_1 if model_name.startswith('magic1') else MAGIC_2)
            print(f"{model_name} Pure: t1={t1} t2={t2} t3={t3} t10={t10} f={f} t={t:.2f}")
        if last_model:
            t1, t2, t3, f, t, t10 = evaluate(mixed_model, query_pairs, choices, model=last_model, reverse_map=map_,
                                             model_name=last_model_name,
                                             magic_string=MAGIC_1 if last_model_name.startswith('magic1') else MAGIC_2)
            print(f"Mixed Model: t1={t1} t2={t2} t3={t3} t10={t10} f={f} t={t:.2f}")
