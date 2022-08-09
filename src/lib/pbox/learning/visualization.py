# -*- coding: UTF-8 -*-
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from contextlib import suppress
from math import ceil
from matplotlib.colors import ListedColormap
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.inspection import DecisionBoundaryDisplay
from sklearn.neighbors import KNeighborsClassifier
from sklearn.tree import export_text, plot_tree


__all__ = ["VISUALIZATIONS"]


cmap_light = ListedColormap(["orange", "cyan", "cornflowerblue"])


def image_dt(classifier, width=5, fontsize=10, logger=None, **params):
    params['filled'] = True
    fig = plt.figure()
    plot_tree(classifier, **params)
    return fig


def image_knn(classifier, logger=None, **params):
    # preprocess data with a PCA in 2D
    X, y = params['data'], params['target']
    X = SimpleImputer(missing_values=np.nan, strategy='mean').fit_transform(X)
    pca = PCA(n_components=2, random_state=42)
    pca.fit(X, y)
    X = pca.transform(X)
    # retrain kNN with this data
    knn = KNeighborsClassifier(**params['algo_params'])
    knn.fit(X, y)
    # now set color map then plot
    labels = list(y.label.unique())
    colors = mpl.cm.get_cmap('jet', len(labels))
    fig, axes = plt.subplots()
    DecisionBoundaryDisplay.from_estimator(knn, X, cmap=colors, ax=axes, alpha=.3,
                                           response_method="predict", plot_method="pcolormesh", shading="auto")
    plt.scatter(X[:, 0], X[:, 1], c=[labels.index(v) for v in y.label.ravel()][::-1], cmap=colors, alpha=1.0)
    return fig


def image_rf(rf, width=5, fontsize=10, logger=None, **params):
    n = len(rf.estimators_)
    rows = ceil(n / width)
    cols = width if rows > 1 else n
    fig, axes = plt.subplots(nrows=rows, ncols=cols, figsize=tuple(map(lambda x: 2*x, (cols, rows))), dpi=900)
    # flatten axes, otherwise it is a matrix of all subplots
    with suppress(TypeError):
        axes = [ax for lst in axes for ax in lst]
    params['filled'] = True
    for i in range(n):
        plot_tree(rf.estimators_[i], ax=axes[i], **params)
        axes[i].set_title("Estimator: %d" % i, fontsize=fontsize)
    return fig


def text_dt(classifier, logger=None, **params):
    return export_text(classifier, **params)


def text_rf(classifier, logger=None, **params):
    s = ""
    for i in range(len(classifier.estimators_)):
        s += "\nEstimator: %d\n" % i
        s += export_text(classifier.estimators_[i], **params)
    return s


VISUALIZATIONS = {
    'DT':  {'image': image_dt, 'text': text_dt},
    'kNN': {'image': image_knn, 'data': True},
    'RF':  {'image': image_rf, 'text': text_rf},
}
