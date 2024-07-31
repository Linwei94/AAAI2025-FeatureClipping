'''
Code to perform feature clipping. Adapted from https://github.com/gpleiss/temperature_scaling
'''
import torch
import numpy as np
from torch import nn, optim
from torch.nn import functional as F

from metrics.metrics import ECELoss


class ModelWithFeatureClipping(nn.Module):
    """
    A thin decorator, which wraps a model with feature clipping.
    model (nn.Module):
        A classification neural network
        NB: Output of the neural network should be the classification logits,
            NOT the softmax (or log softmax)!
    """
    def __init__(self, model):
        super(ModelWithFeatureClipping, self).__init__()
        self.model = model
        self.feature_clip = float("inf")

    def classifier(self, features):
        return self.model.classifier(features)


    def forward(self, input, return_feature=False):
        logits, features = self.model(input, return_feature=True)
        features = self.feature_clipping(features)
        if return_feature:
            return self.classifier(features), features
        return self.classifier(features)



    def feature_clipping(self, features):
        """
        Perform feature clipping on features
        """
        return torch.clamp(features, max=self.feature_clip, min=-self.feature_clip)
    




    def set_feature_clip(self,
                        valid_loader,
                        cross_validate='ece'):
        """
        Tune the feature clipping threshold of the model (using the validation set) with cross-validation on ECE or NLL
        """
        self.cuda()
        self.model.eval()
        nll_criterion = nn.CrossEntropyLoss().cuda()
        ece_criterion = ECELoss().cuda()

        # First: collect all the features and labels for the validation set
        logits_list = []
        labels_list = []
        features_list = []
        with torch.no_grad():
            for input, label in valid_loader:
                input = input.cuda()
                logits, features = self.model(input, return_feature=True)
                logits_list.append(logits)
                labels_list.append(label)
                features_list.append(features)
            logits = torch.cat(logits_list).cuda()
            labels = torch.cat(labels_list).cuda()
            features = torch.cat(features_list).cuda()




        nll_val = 10 ** 7
        ece_val = 10 ** 7
        C_opt_nll = float("inf")
        C_opt_ece = float("inf")
        C = 0.01
        for _ in range(1):
            self.feature_clip = C
            self.cuda()
            after_clipping_nll = nll_criterion(self.classifier(self.feature_clipping(features)), labels).item()
            after_clipping_ece = ece_criterion(self.classifier(self.feature_clipping(features)), labels).item()
            if nll_val > after_clipping_nll:
                C_opt_nll = C
                nll_val = after_clipping_nll

            if ece_val > after_clipping_ece:
                C_opt_ece = C
                ece_val = after_clipping_ece
            C += 0.01

        if cross_validate == 'ece':
            self.feature_clip = C_opt_ece
        else:
            self.feature_clip = C_opt_nll
        self.cuda()

        return self


    def get_feature_clip(self):
        return self.feature_clip
    
# implemented as a post hoc calibrator
class FeatureClippingCalibrator(nn.Module):
    def __init__(self, model, cross_validate='ece'):
        super(FeatureClippingCalibrator, self).__init__()
        self.cross_validate = cross_validate
        self.feature_clip = float("inf")
        self.ece_criterion = ECELoss().cuda()
        self.nll_criterion = nn.CrossEntropyLoss().cuda()
        self.model = model
        self.classifier = self.model.classifier

    def get_feature_clip(self):
        return self.feature_clip
    
    def set_feature_clip(self, features_val, logits_val, labels_val):
        nll_val = float("inf")
        ece_val = float("inf")
        C_opt_nll = float("inf")
        C_opt_ece = float("inf")
        self.feature_clip = float("inf")
        self.feature_clip_bottom = -float("inf")
        cs = []
        eces = []
        accs = []
        nlls = []

        for q in range(2000):
            self.feature_clip = q/100
            logits_after_clipping = self.classifier(self.feature_clipping(features_val))
            after_clipping_nll = self.nll_criterion(logits_after_clipping, labels_val).item()
            after_clipping_ece = self.ece_criterion(logits_after_clipping, labels_val).item()

            if nll_val > after_clipping_nll:
                C_opt_nll = self.feature_clip
                nll_val = after_clipping_nll

            if ece_val > after_clipping_ece:
                C_opt_ece = self.feature_clip
                ece_val = after_clipping_ece

            cs.append(self.feature_clip)
            eces.append(after_clipping_ece)
            nlls.append(after_clipping_nll)
            accs.append((logits_after_clipping.argmax(dim=1) == labels_val).float().mean().item())

        if self.cross_validate == 'ece':
            self.feature_clip = C_opt_ece
        elif self.cross_validate == 'nll':
            self.feature_clip = C_opt_nll


        self.cuda()
        return self
    
    def feature_clipping(self, features):
        """
        Perform feature clipping on logits
        """
        features = torch.clamp(features, min=-self.feature_clip, max=self.feature_clip)
        return features
    

    def forward(self, features):
        return self.classifier(self.feature_clipping(features))


# clip on output logits
class FeatureClippingCalibrator2(nn.Module):
    def __init__(self, model, cross_validate='ece'):
        super(FeatureClippingCalibrator2, self).__init__()
        self.cross_validate = cross_validate
        self.feature_clip = float("inf")
        self.ece_criterion = ECELoss().cuda()
        self.nll_criterion = nn.CrossEntropyLoss().cuda()
        self.model = model
        self.classifier = torch.nn.Identity()

    def get_feature_clip(self):
        return self.feature_clip
    
    def set_feature_clip(self, features_val, logits_val, labels_val):
        nll_val = float("inf")
        ece_val = float("inf")
        C_opt_nll = float("inf")
        C_opt_ece = float("inf")
        self.feature_clip = float("inf")
        self.feature_clip_bottom = -float("inf")
        cs = []
        eces = []
        accs = []
        nlls = []

        for q in range(4000):
            self.feature_clip = q/100
            # pca 分解 features_val, then features_val = features_val - 1st pca component
            # self.ece_criterion(self.classifier(features_val-s[0]*torch.matmul(u[:, 0:1],v[0:1, :])), labels_val).item()
            logits_after_clipping = self.classifier(self.feature_clipping(logits_val))
            after_clipping_nll = self.nll_criterion(logits_after_clipping, labels_val).item()
            after_clipping_ece = self.ece_criterion(logits_after_clipping, labels_val).item()

            if nll_val > after_clipping_nll:
                C_opt_nll = self.feature_clip
                nll_val = after_clipping_nll

            if ece_val > after_clipping_ece:
                C_opt_ece = self.feature_clip
                ece_val = after_clipping_ece

            cs.append(self.feature_clip)
            eces.append(after_clipping_ece)
            nlls.append(after_clipping_nll)
            accs.append((logits_after_clipping.argmax(dim=1) == labels_val).float().mean().item())

        if self.cross_validate == 'ece':
            self.feature_clip = C_opt_ece
        elif self.cross_validate == 'nll':
            self.feature_clip = C_opt_nll


        self.cuda()
        return self
    
    def feature_clipping(self, features):
        """
        Perform feature clipping on logits
        """
        features = torch.clamp(features, min=-self.feature_clip, max=self.feature_clip)
        return features
    

    def forward(self, features):
        return self.classifier(self.feature_clipping(features))
