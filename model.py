import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.autograd import Variable


class ConvInputModel(nn.Module):
    def __init__(self):
        super(ConvInputModel, self).__init__()
        
        self.conv1 = nn.Conv2d(3, 32, 3, stride=2, padding=1)
        self.batchNorm1 = nn.BatchNorm2d(32)
        self.conv2 = nn.Conv2d(32, 64, 3, stride=2, padding=1)
        self.batchNorm2 = nn.BatchNorm2d(64)
        self.conv3 = nn.Conv2d(64, 128, 3, stride=2, padding=1)
        self.batchNorm3 = nn.BatchNorm2d(128)
        self.conv4 = nn.Conv2d(128, 256, 3, stride=2, padding=1)
        self.batchNorm4 = nn.BatchNorm2d(256)

        
    def forward(self, img):
        """convolution"""
        x = self.conv1(img)
        x = F.relu(x)
        x = self.batchNorm1(x)
        x = self.conv2(x)
        x = F.relu(x)
        x = self.batchNorm2(x)
        x = self.conv3(x)
        x = F.relu(x)
        x = self.batchNorm3(x)
        x = self.conv4(x)
        x = F.relu(x)
        x = self.batchNorm4(x)
        return x

  
class FCOutputModel(nn.Module):
    def __init__(self):
        super(FCOutputModel, self).__init__()

        self.fc2 = nn.Linear(512, 1024)
        self.fc3 = nn.Linear(1024, 10)
        # self.fc4 = nn.Linear(250, 10)

        # self.fc2 = nn.Linear(128, 64)
        # self.fc3 = nn.Linear(64, 32)
        # self.fc4 = nn.Linear(32, 10)

    def forward(self, x):
        x = self.fc2(x)
        x = F.dropout(x, 0.05)
        x = F.relu(x)
        x = self.fc3(x)
        # x = F.relu(x)
        # x = self.fc4(x)
        return F.log_softmax(x, dim=1)

class BasicModel(nn.Module):
    def __init__(self, args, name):
        super(BasicModel, self).__init__()
        self.name=name

    def train_(self, input_img, input_state, input_qst, label):
        self.optimizer.zero_grad()
        output = self(input_img, input_state, input_qst)
        loss = F.cross_entropy(output, label)
        loss.backward()
        self.optimizer.step()
        pred = output.data.max(1)[1]
        correct = pred.eq(label.data).cpu().sum()
        accuracy = correct * 100. / len(label)
        return accuracy, loss
        
    def test_(self, input_img, input_state, input_qst, label):
        output = self(input_img, input_state, input_qst)
        loss = F.cross_entropy(output, label)
        pred = output.data.max(1)[1]
        correct = pred.eq(label.data).cpu().sum()
        accuracy = correct * 100. / len(label)
        return accuracy, loss

    def save_model(self, epoch):
        torch.save(self.state_dict(), 'model/epoch_{}_{:02d}.pth'.format(self.name, epoch))


class RN(BasicModel):
    def __init__(self, args):
        super(RN, self).__init__(args, 'RN')
        
        self.conv = ConvInputModel()
        
        self.relation_type = args.relation_type
        self.state_desc = args.state_desc

        ##(number of filters per object+coordinate of object)*2+question vector
        if self.state_desc != 0:
            self.g_fc1 = nn.Linear(7*2+11, 512)
        else:
            self.g_fc1 = nn.Linear(258*2+11, 512)


        self.g_fc2 = nn.Linear(512, 512)
        self.g_fc3 = nn.Linear(512, 512)
        self.g_fc4 = nn.Linear(512, 512)

        self.f_fc1 = nn.Linear(512, 512)

        self.coord_oi = torch.FloatTensor(args.batch_size, 2)
        self.coord_oj = torch.FloatTensor(args.batch_size, 2)
        if args.cuda:
            self.coord_oi = self.coord_oi.cuda()
            self.coord_oj = self.coord_oj.cuda()
        self.coord_oi = Variable(self.coord_oi)
        self.coord_oj = Variable(self.coord_oj)

        # prepare coord tensor
        def cvt_coord(i):
            return [(i/5-2)/2., (i%5-2)/2.]
        
        self.coord_tensor = torch.FloatTensor(args.batch_size, 25, 2)
        if args.cuda:
            self.coord_tensor = self.coord_tensor.cuda()
        self.coord_tensor = Variable(self.coord_tensor)
        np_coord_tensor = np.zeros((args.batch_size, 25, 2))
        for i in range(25):
            np_coord_tensor[:,i,:] = np.array( cvt_coord(i) )
        self.coord_tensor.data.copy_(torch.from_numpy(np_coord_tensor))


        self.fcout = FCOutputModel()
        
        self.optimizer = optim.Adam(self.parameters(), lr=args.lr)


    def forward(self, img, state, qst):
        
        # state matrix input
        if self.state_desc != 0:
            # x = (64 x 6 x 7)
            x = state
            mb = x.size()[0] # batch size
            d = x.size()[1] # object numbers
            n_channels = x.size()[2] # attributes numbers
            x_flat = x.view(mb,n_channels,d).permute(0,2,1) 
        else:
            x = self.conv(img) ## x = (64 x 256 x 5 x 5)
            """g"""
            mb = x.size()[0] # batch size
            n_channels = x.size()[1] # number of filters per object
            d = x.size()[2] # feature map size of dxd
            d *= d
            x_flat = x.view(mb,n_channels,d).permute(0,2,1) # x_flat = (64 x 25 x 256)
            x_flat = torch.cat([x_flat, self.coord_tensor],2) # add coordinates (64 x 25 x 258)
            n_channels += 2
        
        # add question everywhere
        qst = torch.unsqueeze(qst, 1) # (64x1x11)
        qst = qst.repeat(1, d, 1) # (64xdx11)
        qst = torch.unsqueeze(qst, 2) # (64xdx1x11)
        qst = qst.repeat(1,1,d,1) # (64xdxdx11)

        # cast all pairs against each other
        x_i = torch.unsqueeze(x_flat, 1)  # (64x1xdxn_channels)
        x_i = x_i.repeat(1, d, 1, 1)  # (64xdxdxn_channels)
        x_j = torch.unsqueeze(x_flat, 2)  # (64xdx1xn_channels)
        # x_j = torch.cat([x_j, qst], 3) # (64xdx1xn_channels+11)
        x_j = x_j.repeat(1, 1, d, 1)  # (64xdxdxn_channels)
        
        # concatenate all together
        x_full = torch.cat([x_i,x_j],3) # (64xdxdx2*n_channels)
        x_full = torch.cat([x_full, qst], 3) # (64xdxdx2*n_channels+11)
        # reshape for passing through network
        x_ = x_full.view(mb * (d * d), 2*n_channels+11)  # (64*d*dx2*n_channels+11)
        
        x_ = self.g_fc1(x_)
        x_ = F.relu(x_)
        x_ = self.g_fc2(x_)
        x_ = F.relu(x_)
        x_ = self.g_fc3(x_)
        x_ = F.relu(x_)
        x_ = self.g_fc4(x_)
        x_ = F.relu(x_)
        
        # reshape again and sum
        x_g = x_.view(mb, (d * d), 512)

        x_g = x_g.sum(1).squeeze()
        
        """f"""
        x_f = self.f_fc1(x_g)
        x_f = F.relu(x_f)
        
        return self.fcout(x_f)


class CNN_MLP(BasicModel):
    def __init__(self, args):
        super(CNN_MLP, self).__init__(args, 'CNNMLP')

        self.conv  = ConvInputModel()
        self.fc1   = nn.Linear(5*5*24 + 18, 256)  # question concatenated to all
        self.fcout = FCOutputModel()

        self.optimizer = optim.Adam(self.parameters(), lr=args.lr)
        #print([ a for a in self.parameters() ] )
  
    def forward(self, img, qst):
        x = self.conv(img) ## x = (64 x 24 x 5 x 5)

        """fully connected layers"""
        x = x.view(x.size(0), -1)
        
        x_ = torch.cat((x, qst), 1)  # Concat question
        
        x_ = self.fc1(x_)
        x_ = F.relu(x_)
        
        return self.fcout(x_)
