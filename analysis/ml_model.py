"""
analysis/ml_model.py - Unsupervised Pattern Detection for KLSE News Trader

Uses:
- KMeans clustering to group stocks by feature similarity
- Isolation Forest for anomaly detection (unusual news+price patterns)
- Combined scoring 1-10 for each stock
"""

import sqlite3
import numpy as np
from datetime import datetime
from collections import defaultdict

import os
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "klse.db")

# Feature columns used for ML
FEATURE_COLUMNS = [
    'momentum_5d', 'momentum_10d', 'momentum_20d', 'momentum_50d',
    'volatility_20d', 'volume_change_20d', 'news_sentiment_10d',
    'news_frequency_spike', 'rsi_14d', 'ma_crossover_signal'
]


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_feature_matrix(conn):
    """Load latest features for all stocks into a matrix."""
    rows = conn.execute("""
        SELECT symbol, momentum_5d, momentum_10d, momentum_20d, momentum_50d,
               volatility_20d, volume_change_20d, news_sentiment_10d,
               news_frequency_spike, rsi_14d, ma_crossover_signal
        FROM ml_features
        WHERE date = (SELECT MAX(date) FROM ml_features)
    """).fetchall()
    
    if not rows:
        return [], np.array([])
    
    symbols = [row['symbol'] for row in rows]
    matrix = np.array([
        [row[col] if row[col] is not None else 0.0 for col in FEATURE_COLUMNS]
        for row in rows
    ])
    
    return symbols, matrix


def normalize_features(matrix):
    """Z-score normalization."""
    if matrix.size == 0:
        return matrix
    means = np.mean(matrix, axis=0)
    stds = np.std(matrix, axis=0)
    stds[stds == 0] = 1.0  # avoid division by zero
    return (matrix - means) / stds


class KMeansClustering:
    """Simple KMeans implementation."""
    
    def __init__(self, n_clusters=4, max_iter=100, random_state=42):
        self.n_clusters = n_clusters
        self.max_iter = max_iter
        self.random_state = random_state
        self.centroids = None
        self.labels = None
    
    def fit(self, X):
        """Fit KMeans to data."""
        np.random.seed(self.random_state)
        n_samples, n_features = X.shape
        
        # Initialize centroids randomly from data points
        indices = np.random.choice(n_samples, self.n_clusters, replace=False)
        self.centroids = X[indices].copy()
        
        for _ in range(self.max_iter):
            # Assign clusters
            distances = self._compute_distances(X)
            labels = np.argmin(distances, axis=1)
            
            # Update centroids
            new_centroids = np.zeros_like(self.centroids)
            for k in range(self.n_clusters):
                mask = labels == k
                if np.any(mask):
                    new_centroids[k] = X[mask].mean(axis=0)
                else:
                    new_centroids[k] = self.centroids[k]
            
            # Check convergence
            if np.allclose(self.centroids, new_centroids):
                break
            self.centroids = new_centroids
        
        self.labels = labels
        return self
    
    def _compute_distances(self, X):
        """Compute Euclidean distance from each point to each centroid."""
        distances = np.zeros((X.shape[0], self.n_clusters))
        for k in range(self.n_clusters):
            distances[:, k] = np.sum((X - self.centroids[k]) ** 2, axis=1)
        return distances
    
    def predict(self, X):
        """Predict cluster assignments."""
        distances = self._compute_distances(X)
        return np.argmin(distances, axis=1)


class IsolationForest:
    """Simple Isolation Forest implementation for anomaly detection."""
    
    def __init__(self, n_trees=100, sample_size=256, contamination=0.1, random_state=42):
        self.n_trees = n_trees
        self.sample_size = min(sample_size, 256)
        self.contamination = contamination
        self.random_state = random_state
        self.trees = []
        self.threshold = None
    
    def fit(self, X):
        """Build isolation trees."""
        np.random.seed(self.random_state)
        n_samples = X.shape[0]
        sample_size = min(self.sample_size, n_samples)
        
        self.trees = []
        for _ in range(self.n_trees):
            # Random subsample
            indices = np.random.choice(n_samples, sample_size, replace=False)
            sample = X[indices]
            tree = self._build_tree(sample, 0, max_depth=int(np.ceil(np.log2(sample_size))))
            self.trees.append(tree)
        
        # Compute anomaly scores and threshold
        scores = self.decision_function(X)
        # Anomalies have higher scores (shorter path length = more anomalous)
        self.threshold = np.percentile(scores, 100 * (1 - self.contamination))
        return self
    
    def _build_tree(self, X, current_depth, max_depth):
        """Recursively build an isolation tree."""
        n_samples, n_features = X.shape
        
        if current_depth >= max_depth or n_samples <= 1:
            return {'type': 'leaf', 'size': n_samples}
        
        # Random feature and split
        feature_idx = np.random.randint(0, n_features)
        min_val = X[:, feature_idx].min()
        max_val = X[:, feature_idx].max()
        
        if min_val == max_val:
            return {'type': 'leaf', 'size': n_samples}
        
        split_val = np.random.uniform(min_val, max_val)
        
        left_mask = X[:, feature_idx] < split_val
        right_mask = ~left_mask
        
        return {
            'type': 'internal',
            'feature': feature_idx,
            'split': split_val,
            'left': self._build_tree(X[left_mask], current_depth + 1, max_depth),
            'right': self._build_tree(X[right_mask], current_depth + 1, max_depth),
        }
    
    def _path_length(self, x, tree, current_depth):
        """Compute path length for a single sample."""
        if tree['type'] == 'leaf':
            return current_depth + self._c(tree['size'])
        
        if x[tree['feature']] < tree['split']:
            return self._path_length(x, tree['left'], current_depth + 1)
        else:
            return self._path_length(x, tree['right'], current_depth + 1)
    
    def _c(self, n):
        """Average path length for unsuccessful search in BST."""
        if n <= 1:
            return 0
        return 2.0 * (np.log(n - 1) + 0.5772156649) - (2.0 * (n - 1) / n)
    
    def decision_function(self, X):
        """Compute anomaly scores (higher = more anomalous)."""
        n_samples = X.shape[0]
        avg_path_lengths = np.zeros(n_samples)
        
        for i in range(n_samples):
            path_sum = 0
            for tree in self.trees:
                path_sum += self._path_length(X[i], tree, 0)
            avg_path_lengths[i] = path_sum / self.n_trees
        
        # Anomaly score: 0.5 means normal, closer to 1 = anomaly
        c_n = self._c(self.sample_size)
        scores = 2 ** (-avg_path_lengths / c_n)
        return scores
    
    def predict(self, X):
        """Predict: 1 for anomaly, 0 for normal."""
        scores = self.decision_function(X)
        return (scores >= self.threshold).astype(int)


def score_stock(features_dict, cluster_label, anomaly_score, cluster_scores):
    """
    Score each stock 1-10 based on:
    - Cluster quality (which cluster it belongs to)
    - Anomaly score (unusual patterns)
    - Individual feature strength
    """
    score = 5.0  # baseline
    
    # Momentum contribution (positive momentum = good)
    mom_score = 0
    for key in ['momentum_5d', 'momentum_10d', 'momentum_20d']:
        val = features_dict.get(key, 0)
        if val is not None:
            mom_score += val * 10  # scale up
    score += min(max(mom_score, -2), 2)
    
    # News sentiment contribution
    sentiment = features_dict.get('news_sentiment_10d', 0)
    if sentiment is not None:
        score += sentiment * 1.5
    
    # RSI contribution (oversold = opportunity)
    rsi = features_dict.get('rsi_14d', 50)
    if rsi is not None:
        if rsi < 30:
            score += 1.5  # oversold - potential buy
        elif rsi > 70:
            score -= 1.0  # overbought
    
    # MA crossover signal
    ma_signal = features_dict.get('ma_crossover_signal', 0)
    if ma_signal is not None:
        score += ma_signal * 0.5
    
    # Volume spike with positive sentiment = strong signal
    vol_change = features_dict.get('volume_change_20d', 0)
    news_freq = features_dict.get('news_frequency_spike', 1)
    if vol_change and vol_change > 0.2 and sentiment and sentiment > 0:
        score += 0.5
    
    # Anomaly bonus (unusual patterns can be opportunities)
    if anomaly_score > 0.6:
        score += 0.5
    
    # Cluster quality adjustment
    if cluster_label in cluster_scores:
        score += cluster_scores[cluster_label] * 0.3
    
    # Clamp to 1-10
    return max(1.0, min(10.0, round(score, 1)))


def evaluate_clusters(symbols, features_matrix, labels, features_dicts):
    """Evaluate cluster quality - which clusters have better stocks."""
    cluster_scores = defaultdict(float)
    cluster_counts = defaultdict(int)
    
    for i, (symbol, label) in enumerate(zip(symbols, labels)):
        fd = features_dicts[i]
        # Positive cluster = positive momentum + positive sentiment
        cluster_momentum = fd.get('momentum_20d', 0) or 0
        cluster_sentiment = fd.get('news_sentiment_10d', 0) or 0
        quality = cluster_momentum * 5 + cluster_sentiment
        cluster_scores[label] += quality
        cluster_counts[label] += 1
    
    # Average
    for label in cluster_scores:
        if cluster_counts[label] > 0:
            cluster_scores[label] /= cluster_counts[label]
    
    return dict(cluster_scores)


def run_ml_analysis():
    """Run full ML analysis pipeline."""
    conn = get_db()
    
    # Load features
    symbols, feature_matrix = get_feature_matrix(conn)
    
    if not symbols or feature_matrix.size == 0:
        print("No features found. Run feature engineering first.")
        conn.close()
        return []
    
    # Normalize
    normalized = normalize_features(feature_matrix)
    
    # Build feature dicts for scoring
    features_dicts = []
    for i, symbol in enumerate(symbols):
        fd = {'symbol': symbol}
        for j, col in enumerate(FEATURE_COLUMNS):
            fd[col] = float(feature_matrix[i, j])
        features_dicts.append(fd)
    
    # KMeans clustering
    n_clusters = min(4, len(symbols))
    kmeans = KMeansClustering(n_clusters=n_clusters)
    kmeans.fit(normalized)
    cluster_labels = kmeans.predict(normalized)
    
    # Isolation Forest anomaly detection
    iso_forest = IsolationForest(n_trees=100, contamination=0.15)
    iso_forest.fit(normalized)
    anomaly_scores = iso_forest.decision_function(normalized)
    anomaly_labels = iso_forest.predict(normalized)
    
    # Evaluate clusters
    cluster_scores = evaluate_clusters(symbols, feature_matrix, cluster_labels, features_dicts)
    
    # Score each stock
    results = []
    for i, symbol in enumerate(symbols):
        score = score_stock(
            features_dicts[i],
            cluster_labels[i],
            anomaly_scores[i],
            cluster_scores
        )
        
        results.append({
            'symbol': symbol,
            'cluster': int(cluster_labels[i]),
            'anomaly_score': float(anomaly_scores[i]),
            'is_anomaly': bool(anomaly_labels[i]),
            'ml_score': score,
            'features': features_dicts[i]
        })
    
    conn.close()
    return results


if __name__ == "__main__":
    results = run_ml_analysis()
    print(f"ML Analysis complete for {len(results)} stocks")
    for r in sorted(results, key=lambda x: x['ml_score'], reverse=True)[:10]:
        anomaly_str = " [ANOMALY]" if r['is_anomaly'] else ""
        print(f"  {r['symbol']}: score={r['ml_score']}, cluster={r['cluster']}{anomaly_str}")
