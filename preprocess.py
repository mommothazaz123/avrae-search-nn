"""
Sorts a raw type query file into mapped training data (ready to plug in to keras)
Input: Raw type query file (in training/unprocessed/[BATCH]_[TYPE].json)
       Result objects file (in res/[TYPE].json)
Outputs: mapped training file, in training/[BATCH]_[TYPE].json
         map file, in preprocessing/map-[BATCH]_[TYPE].json
"""
import collections
import json
import time

MAGIC_1 = "abcdefghijklmnopqrstuvwxyz '"
MAGIC_2 = "qwertyuiopasdfghjkl'zxcvbnm "
# MAGIC_2 = "aqzswxdecfrvgtb hynjumkilop'"
INPUT_LENGTH = 16


def load_type_query_file(name):
    """
    Loads a list of unprocessed type queries from a file in training/unprocessed.
    {
        "query": string,
        "result": string
    }
    """
    with open(f"training/unprocessed/{name}") as f:
        data = json.load(f)
    print(f"Loaded {len(data)} queries from {name}")
    return data


def map_data(queries, filename):
    """
    Generates a map (i -> name) and reverse map (name -> i), and modifies queries to set result to an int.
    Output:
    {
        "query": string,
        "result": int
    }
    """
    typename = filename.split('_')[-1]
    with open(f"res/{typename}") as f:
        result_objs = json.load(f)
    srd_result_objs = [o for o in result_objs if o.get('srd')]

    # generate map
    print("Generating map...")
    mapped = {}
    reverse_map = {}
    for i, entry in enumerate(result_objs):
        mapped[i] = entry['name']
        reverse_map[entry['name']] = i

    # dump map
    print("Dumping map...")
    with open(f"preprocessing/map-{filename}", 'w') as f:
        json.dump(mapped, f, indent=2)

    # and SRD
    srd_mapped = {}
    srd_reverse_map = {}
    for i, entry in enumerate(srd_result_objs):
        srd_mapped[i] = entry['name']
        srd_reverse_map[entry['name']] = i

    # dump map
    print("Dumping SRD map...")
    with open(f"preprocessing/map-srd-{filename}", 'w') as f:
        json.dump(srd_mapped, f, indent=2)

    # map training
    print("Mapping queries...")

    for entry in queries:
        res = entry['result']
        entry['result'] = reverse_map[res]
        entry['srd_result'] = srd_reverse_map.get(res)

    print("Done mapping.")
    return mapped, reverse_map


def ensure_at_least_1(data, reverse_map):
    for name, i in reverse_map.items():
        data.append({"query": name, "result": i})


def clean_queries(data):
    print("Cleaning queries...")
    for entry in data:
        entry['query'] = clean(entry['query'])
    print("Done.")


def clean(query):
    filtered = query.lower()
    filtered = ''.join(c for c in filtered if c in MAGIC_1)
    return filtered[:INPUT_LENGTH].strip()


def clean_dupes(data, srd=False):
    """
    Cleans up a long list of queries into what each query returned.
    output:
    {
        "[query]": {counter of results (int: int)}
    }
    """
    print(f"Cleaning up duplicates (srd={srd})...")
    queries = collections.defaultdict(lambda: collections.Counter())
    for entry in data:
        query = entry['query']
        if not srd:
            result = entry['result']
        else:
            result = entry['srd_result']
            if result is None:
                continue
        queries[query][result] += 1
    print(f"Cleaned {len(data)} entries into {len(queries)}.")
    return queries


def dump_evaluation(cleaned, filename):
    print("Writing evaluation file...")
    out = []
    for query, results in cleaned.items():
        for result in results.keys():
            out.append({'query': query, 'result': result})
    with open(f'preprocessing/evaluation-{filename}', 'w') as f:
        json.dump(out, f)
    print("Done writing evaluation.")


def dump_training(cleaned, filename, num_results):
    print("Formatting for training...")
    out1 = []
    out2 = []
    out_embedding = []
    for query, results in cleaned.items():
        tokenized = tokenize(query, MAGIC_1)
        tokenized2 = tokenize(query, MAGIC_2)
        result_vec = generate_y_vector(results, num_results)
        out1.append({'x': tokenized, 'y': result_vec})
        out2.append({'x': tokenized2, 'y': result_vec})
        out_embedding.append({'x': tokenize(query, MAGIC_1, True), 'y': result_vec})
    with open(f'training/1-{filename}', 'w') as f:
        json.dump(out1, f)
    with open(f'training/2-{filename}', 'w') as f:
        json.dump(out2, f)
    with open(f'training/embedding-{filename}', 'w') as f:
        json.dump(out_embedding, f)
    print("Done formatting.")


def dump_training_2(data, filename):
    print("Formatting for naive training...")
    out = []
    for entry in data:
        tokenized = tokenize(entry['query'], MAGIC_2)
        result = entry['result']
        out.append({'x': tokenized, 'y': result})
    with open(f'training/naive-{filename}', 'w') as f:
        json.dump(out, f)
    print("Done formatting.")


def dump_srd(cleaned, filename):
    print("Formatting for srd training...")
    with open(f"preprocessing/map-srd-{filename}", 'r') as f:
        srd_map = json.load(f)
    out = []
    for query, results in cleaned.items():
        result_vec = generate_y_vector(results, len(srd_map))
        out.append({'x': tokenize(query, MAGIC_1, True), 'y': result_vec})
    with open(f'training/embedding-srd-{filename}', 'w') as f:
        json.dump(out, f)
    print("Done formatting.")


def tokenize(query, magic_string, use_index=False):
    num_chars = len(magic_string)
    if not use_index:
        tokenized = [0.] * INPUT_LENGTH
        for i, char in enumerate(query):
            tokenized[i] = (magic_string.index(char) + 1) / num_chars
    else:
        tokenized = [0] * INPUT_LENGTH
        for i, char in enumerate(query):
            tokenized[i] = magic_string.index(char) + 1
    return tokenized


def generate_y_vector(results, num_results):
    """Given a count of results and the total number of results, returns a normalized label vector."""
    vec = [0.] * num_results
    for i, count in results.items():
        vec[i] = count
    vec_sum = sum(vec)
    for i, _ in enumerate(vec):
        vec[i] /= vec_sum
    return vec


if __name__ == '__main__':
    filename = input("Filename: ").strip()
    starttime = time.time()
    data = load_type_query_file(filename)
    map_, reverse_map = map_data(data, filename)
    # ensure_at_least_1(data, reverse_map)
    clean_queries(data)
    cleaned = clean_dupes(data)
    srd_cleaned = clean_dupes(data, True)
    dump_evaluation(cleaned, filename)
    dump_evaluation(srd_cleaned, f"srd-{filename}")
    dump_training(cleaned, filename, len(map_))
    dump_training_2(data, filename)
    dump_srd(srd_cleaned, filename)

    endtime = time.time()
    print(f"Done! Took {endtime-starttime:.3f} seconds.")
